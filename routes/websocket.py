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
from services.agent import process_user_message
from services.conetion_manager import ConnectionState, manager
from services.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter()


async def send(state: ConnectionState, msg) -> None:
    try:
        await state.websocket.send_text(msg.model_dump_json())
    except Exception as exc:
        logger.warning(f"[WS] Failed to send message: {exc}")


async def run_agent(state: ConnectionState, text: str) -> None:
    logger.info(f"[AGENT] Processing: '{text}' | slide={state.current_slide} | deck_slides={state.deck.total}")
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
                logger.info("[AGENT] Cancelled mid-stream")
                break

            if result.slide_change is not None:
                logger.info(f"[AGENT] Slide change → {result.slide_change} (reason: {result.slide_reason})")
                state.current_slide = result.slide_change
                await send(state, ChangeSlideMessage(
                    index=result.slide_change,
                    reason=result.slide_reason,
                ))

            if result.spoken_text:
                logger.info(f"[AGENT] Speaking: '{result.spoken_text[:80]}{'...' if len(result.spoken_text) > 80 else ''}'")
                await send(state, StatusMessage(state="speaking"))
                await send(state, SpeakMessage(text=result.spoken_text))

    except asyncio.CancelledError:
        logger.info("[AGENT] Task cancelled")
    except Exception as exc:
        logger.exception(f"[AGENT] Unexpected error: {exc}")
    finally:
        if not state.cancel_event.is_set():
            await send(state, StatusMessage(state="idle"))
            logger.info("[AGENT] Done, status → idle")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    state = await manager.connect(websocket)
    client = websocket.client
    logger.info(f"[WS] Client connected: {client}")
    await send(state, StatusMessage(state="idle"))

    try:
        while True:
            raw = await websocket.receive_text()
            logger.debug(f"[WS] Received: {raw[:200]}")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[WS] Invalid JSON: {raw[:100]}")
                continue

            msg_type = data.get("type")
            logger.info(f"[WS] Message type: '{msg_type}'")

            if msg_type == "load_deck":
                session_id = data.get("session_id", "")
                logger.info(f"[WS] load_deck request: session_id={session_id}")
                deck = session_store.get(session_id)
                if deck:
                    state.load_deck(deck)
                    logger.info(f"[WS] Deck loaded: {deck.total} slides")
                    await send(state, StatusMessage(state="idle"))
                else:
                    logger.warning(f"[WS] Session not found: {session_id}")
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Session not found. Please re-upload."})
                    )

            elif msg_type == "user_speech":
                text = (data.get("text") or "").strip()
                if not text:
                    logger.debug("[WS] Empty user_speech, ignoring")
                    continue
                logger.info(f"[WS] user_speech: '{text}'")
                state.interrupt()
                state.reset_cancel()
                state.agent_task = asyncio.create_task(run_agent(state, text))

            elif msg_type == "interrupt":
                logger.info("[WS] Interrupt received")
                state.interrupt()
                state.reset_cancel()
                await send(state, InterruptedMessage())
                await send(state, StatusMessage(state="idle"))

            elif msg_type == "slide_changed":
                idx = data.get("index")
                if isinstance(idx, int):
                    logger.info(f"[WS] Manual slide change → {idx}")
                    state.current_slide = idx

            else:
                logger.warning(f"[WS] Unknown message type: '{msg_type}'")

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: {client}")
        manager.disconnect(websocket)
    except Exception as exc:
        logger.exception(f"[WS] Unexpected error: {exc}")
        manager.disconnect(websocket)