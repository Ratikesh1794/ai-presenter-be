from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from models.messages import (
    ChangeSlideMessage,
    InterruptedMessage,
    SpeakMessage,
    StatusMessage,
)
from services.agent import (
    answer_doubt,
    generate_intro,
    present_slide,
    process_user_message,
)
from services.conetion_manager import ConnectionState, PresentationMode, manager
from services.session_store import session_store

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def send(state: ConnectionState, msg) -> None:
    try:
        await state.websocket.send_text(msg.model_dump_json())
    except Exception as exc:
        logger.warning(f"[WS] Send failed: {exc}")


async def _emit_result(state: ConnectionState, result) -> None:
    """Send slide change + spoken text from an AgentResult to the client."""
    if result.slide_change is not None:
        state.current_slide = result.slide_change
        await send(state, ChangeSlideMessage(
            index=result.slide_change,
            reason=result.slide_reason,
        ))

    if result.spoken_text:
        await send(state, StatusMessage(state="speaking"))
        await send(state, SpeakMessage(text=result.spoken_text))

        # Wait for TTS to finish on frontend — estimate ~130 wpm
        word_count = len(result.spoken_text.split())
        tts_duration = max(2.0, (word_count / 130) * 60)
        logger.info(f"[WS] Waiting {tts_duration:.1f}s for TTS ({word_count} words)")
        await asyncio.sleep(tts_duration)


# ─── Presentation loop ────────────────────────────────────────────────────────

async def run_presentation(state: ConnectionState) -> None:
    """
    Full auto-presenting loop:
    1. Generate greeting + navigate to slide 0
    2. Loop: present current slide → agent calls change_slide → repeat
    3. Stop when agent calls presentation_complete or all slides done
    """
    logger.info("[PRES] Starting presentation loop")
    state.mode = PresentationMode.PRESENTING
    state.presentation_started = True

    await send(state, StatusMessage(state="thinking"))

    try:
        # ── Intro ─────────────────────────────────────────────────────────────
        async for result in generate_intro(
            deck=state.deck,
            conversation_history=state.conversation_history,
            cancel_event=state.cancel_event,
        ):
            if state.cancel_event.is_set():
                return
            await _emit_result(state, result)

        if state.cancel_event.is_set():
            return

        # ── Slide-by-slide loop ───────────────────────────────────────────────
        while (
            state.mode == PresentationMode.PRESENTING
            and state.current_slide < state.deck.total
            and not state.cancel_event.is_set()
        ):
            slide_idx = state.current_slide
            logger.info(f"[PRES] Presenting slide {slide_idx}/{state.deck.total - 1}")

            await send(state, StatusMessage(state="thinking"))

            async for result in present_slide(
                slide_index=slide_idx,
                deck=state.deck,
                conversation_history=state.conversation_history,
                cancel_event=state.cancel_event,
            ):
                if state.cancel_event.is_set():
                    return

                await _emit_result(state, result)

                if result.presentation_complete:
                    logger.info("[PRES] Presentation complete!")
                    state.mode = PresentationMode.COMPLETE
                    await send(state, StatusMessage(state="idle"))
                    # Notify frontend presentation is done
                    await state.websocket.send_text(json.dumps({
                        "type": "presentation_complete"
                    }))
                    return

            if state.cancel_event.is_set():
                return

            # Small pause between slides
            await asyncio.sleep(1.0)

    except asyncio.CancelledError:
        logger.info("[PRES] Presentation cancelled (likely interrupted by user)")
    except Exception as exc:
        logger.exception(f"[PRES] Error: {exc}")
    finally:
        if not state.cancel_event.is_set():
            await send(state, StatusMessage(state="idle"))


# ─── Doubt handler ────────────────────────────────────────────────────────────

async def run_doubt(state: ConnectionState, question: str) -> None:
    """
    Answer a user's doubt then automatically resume the presentation.
    """
    logger.info(f"[DOUBT] Question: '{question}' | will resume at slide {state.resume_slide}")
    state.mode = PresentationMode.ANSWERING_DOUBT

    await send(state, StatusMessage(state="thinking"))

    try:
        async for result in answer_doubt(
            question=question,
            current_slide=state.current_slide,
            resume_slide=state.resume_slide,
            deck=state.deck,
            conversation_history=state.conversation_history,
            cancel_event=state.cancel_event,
        ):
            if state.cancel_event.is_set():
                return
            await _emit_result(state, result)

        if state.cancel_event.is_set():
            return

        # ── Auto-resume presentation ──────────────────────────────────────────
        logger.info(f"[DOUBT] Doubt answered. Resuming presentation from slide {state.resume_slide}")
        state.mode = PresentationMode.PRESENTING

        # Navigate back to resume slide if we drifted
        if state.current_slide != state.resume_slide:
            state.current_slide = state.resume_slide
            await send(state, ChangeSlideMessage(
                index=state.resume_slide,
                reason="Resuming presentation",
            ))
            await asyncio.sleep(0.5)

        # Kick off the slide loop again from resume point
        while (
            state.mode == PresentationMode.PRESENTING
            and state.current_slide < state.deck.total
            and not state.cancel_event.is_set()
        ):
            slide_idx = state.current_slide
            logger.info(f"[PRES] Resuming slide {slide_idx}")

            await send(state, StatusMessage(state="thinking"))

            async for result in present_slide(
                slide_index=slide_idx,
                deck=state.deck,
                conversation_history=state.conversation_history,
                cancel_event=state.cancel_event,
            ):
                if state.cancel_event.is_set():
                    return
                await _emit_result(state, result)

                if result.presentation_complete:
                    state.mode = PresentationMode.COMPLETE
                    await send(state, StatusMessage(state="idle"))
                    await state.websocket.send_text(json.dumps({
                        "type": "presentation_complete"
                    }))
                    return

            await asyncio.sleep(1.0)

    except asyncio.CancelledError:
        logger.info("[DOUBT] Cancelled")
    except Exception as exc:
        logger.exception(f"[DOUBT] Error: {exc}")
    finally:
        if not state.cancel_event.is_set():
            await send(state, StatusMessage(state="idle"))


# ─── Ad-hoc Q&A (before presentation starts) ─────────────────────────────────

async def run_adhoc(state: ConnectionState, text: str) -> None:
    await send(state, StatusMessage(state="thinking"))
    try:
        async for result in process_user_message(
            text=text,
            current_slide=state.current_slide,
            deck=state.deck,
            conversation_history=state.conversation_history,
            cancel_event=state.cancel_event,
        ):
            if state.cancel_event.is_set():
                return
            if result.slide_change is not None:
                state.current_slide = result.slide_change
                await send(state, ChangeSlideMessage(
                    index=result.slide_change,
                    reason=result.slide_reason,
                ))
            if result.spoken_text:
                await send(state, StatusMessage(state="speaking"))
                await send(state, SpeakMessage(text=result.spoken_text))
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception(f"[ADHOC] Error: {exc}")
    finally:
        if not state.cancel_event.is_set():
            await send(state, StatusMessage(state="idle"))


# ─── WebSocket endpoint ───────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    state = await manager.connect(websocket)
    logger.info(f"[WS] Connected: {websocket.client}")
    await send(state, StatusMessage(state="idle"))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")
            logger.info(f"[WS] ← {msg_type}")

            # ── load_deck ─────────────────────────────────────────────────────
            if msg_type == "load_deck":
                session_id = data.get("session_id", "")
                session = session_store.get(session_id)
                if session:
                    state.load_deck(session.deck)
                    logger.info(f"[WS] Deck loaded: {session.deck.total} slides")
                    await send(state, StatusMessage(state="idle"))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Session not found. Please re-upload."
                    }))

            # ── start_presentation ────────────────────────────────────────────
            elif msg_type == "start_presentation":
                logger.info("[WS] Starting presentation")
                state.interrupt()
                state.reset_cancel()
                state.current_slide = 0
                state.conversation_history = []
                state.agent_task = asyncio.create_task(run_presentation(state))

            # ── user_speech ───────────────────────────────────────────────────
            elif msg_type == "user_speech":
                text = (data.get("text") or "").strip()
                if not text:
                    continue

                if state.presentation_started and state.mode == PresentationMode.PRESENTING:
                    # User interrupted mid-presentation — save where we were
                    state.resume_slide = state.current_slide
                    logger.info(f"[WS] Doubt during presentation. Saving resume_slide={state.resume_slide}")
                    state.interrupt()
                    state.reset_cancel()
                    state.agent_task = asyncio.create_task(run_doubt(state, text))

                elif state.presentation_started and state.mode == PresentationMode.ANSWERING_DOUBT:
                    # Another doubt while answering — just answer this one
                    state.interrupt()
                    state.reset_cancel()
                    state.agent_task = asyncio.create_task(run_doubt(state, text))

                else:
                    # Pre-presentation Q&A
                    state.interrupt()
                    state.reset_cancel()
                    state.agent_task = asyncio.create_task(run_adhoc(state, text))

            # ── interrupt ─────────────────────────────────────────────────────
            elif msg_type == "interrupt":
                logger.info(f"[WS] Interrupt — mode was {state.mode}")
                state.interrupt()
                state.reset_cancel()
                await send(state, InterruptedMessage())
                await send(state, StatusMessage(state="idle"))

            # ── slide_changed (manual navigation) ────────────────────────────
            elif msg_type == "slide_changed":
                idx = data.get("index")
                if isinstance(idx, int):
                    logger.info(f"[WS] Manual slide sync → {idx}")
                    state.current_slide = idx

    except WebSocketDisconnect:
        logger.info(f"[WS] Disconnected: {websocket.client}")
        manager.disconnect(websocket)
    except Exception as exc:
        logger.exception(f"[WS] Error: {exc}")
        manager.disconnect(websocket)