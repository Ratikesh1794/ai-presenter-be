from __future__ import annotations

import asyncio
import json
import logging
import os

from openai import AsyncOpenAI

from models.slides import Deck
from services.cost_tracker import get_cost_tracker

logger = logging.getLogger(__name__)
_client = AsyncOpenAI(api_key=os.environ["LLM_API_KEY"])
_cost_tracker = get_cost_tracker()

# ─── Tools ────────────────────────────────────────────────────────────────────

def _build_tools(deck: Deck) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "change_slide",
                "description": (
                    "Navigate to a specific slide. Use this when moving to the next slide "
                    "during the presentation, or when a user question is best answered by "
                    "a different slide."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slide_index": {
                            "type": "integer",
                            "description": f"Zero-based index (0–{deck.total - 1})",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why navigating to this slide",
                        },
                    },
                    "required": ["slide_index", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "presentation_complete",
                "description": (
                    "Call this when you have finished presenting ALL slides "
                    "and delivered a closing summary. Signals the end of the presentation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "One-sentence closing remark",
                        },
                    },
                    "required": ["summary"],
                },
            },
        },
    ]


# ─── System prompts ───────────────────────────────────────────────────────────

def _presenter_system_prompt(deck: Deck, current_slide: int) -> str:
    return f"""You are Presento — an AI presentation agent — delivering a live presentation created by the person(s) named in the deck.
You are on slide {current_slide} of {deck.total - 1} (zero-indexed).

SLIDES:
{deck.get_agent_context()}

PRESENTATION MODE (AUTO-PRESENT):
- Always identify yourself as "Presento, an AI presentation agent." Never present yourself as the slide creator or claim authorship of the slides.
- If the deck explicitly lists a creator/author field (e.g., "Creator:", "Author:", or a name on the title slide), immediately attribute the work by saying: "This presentation was created by <exact creator name(s) as listed> — I am Presento and I will present it for them." If no explicit name is present, say "the author(s) listed in the deck."
- For each slide, speak 3–5 sentences only. Structure each micro-speech as a short paragraph with:
  1) one sharp headline (the single insight),
  2) one concise explanation that gives a quick example or analogy if useful,
  3) one practical takeaway or next step the audience can hold on to.
- Do NOT read slide text verbatim — synthesize and highlight the most important idea.
- Use natural, varied cadence and vivid but precise language so the presentation feels human and energetic — avoid monotone and long enumerations.
- No bullet lists or markdown in spoken output; speak as fluid sentences.
- After finishing a slide, ALWAYS call change_slide() and then continue presenting the next slide.
- When all slides have been presented ({deck.total} total), call presentation_complete().
- Never pause to ask permission or ask the user if they are ready — keep a steady, professional flow.
CURRENT STATE: Presenting slide {current_slide}. Continue from here."""

def _doubt_system_prompt(deck: Deck, current_slide: int, resume_slide: int) -> str:
    return f"""You are Presento — an AI presentation agent — answering a live question the audience asked on slide {current_slide}.
You are currently on slide {current_slide} of {deck.total - 1}.

INTERRUPTION / DOUBT-ANSWERING RULES:
- Start by briefly restating the question in one clause, then give a focused, factual answer in 2–4 sentences.
- If you must reference slide authorship or ownership, attribute clearly: "This material was created by <creator name(s) from the deck>; I (Presento) am presenting it on their behalf."
- If the best answer requires jumping to another slide, call change_slide() to navigate to that slide, answer there concisely (2–4 sentences), then call change_slide() to return to slide {resume_slide}.
- Keep the response tightly focused — give only the missing detail or clarification the asker needs, do not re-present the whole slide.
- End with a short transition like: "Now, picking up where we left off..." and then resume the presentation flow.
CURRENT STATE: Interrupted on slide {current_slide} — answer the user's question, then transition back to slide {resume_slide}."""

def _intro_system_prompt(deck: Deck) -> str:
    return f"""You are Presento — an AI presentation agent — about to start a live session with slides created by the person(s) named in the deck.
SLIDES:
{deck.get_agent_context()}

INTRODUCTION RULES:
- Start with a striking single-line hook that clearly states the presentation's purpose and benefit (example: "What if your next slide could convince decision-makers in under 60 seconds?").
- Immediately follow with a warm, confident greeting that identifies you and the creators: "Hello — I'm Presento, an AI presentation agent. This presentation was created by <read the creator name(s) exactly as listed on the deck or title slide> and I'm presenting it on their behalf."
- Give a concise 2–3 sentence roadmap that states what will be covered and the top 2–3 takeaways the audience should walk away with. Keep this pointed and outcome-focused.
- End with "Let's get started!" then immediately call change_slide() to navigate to slide 0 and begin presenting.
- Keep the entire intro under 4 sentences and do NOT wait for any response — begin immediately."""


# ─── Agent result ─────────────────────────────────────────────────────────────

class AgentResult:
    def __init__(self) -> None:
        self.slide_change: int | None = None
        self.slide_reason: str = ""
        self.spoken_text: str = ""
        self.presentation_complete: bool = False
        self.should_continue_presenting: bool = False  # signal to keep auto-advancing
        self.current_session_cost: dict | None = None  # optional: cost summary at this point


# ─── Core LLM call ───────────────────────────────────────────────────────────

async def _llm_call(
    messages: list[dict],
    deck: Deck,
    cancel_event: asyncio.Event,
    max_tokens: int = 1024,
) -> tuple[object, list[dict]]:
    """Single OpenAI call. Returns (assistant_message, tool_results)."""
    if cancel_event.is_set():
        return None, []

    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        tools=_build_tools(deck),
        tool_choice="auto",
        messages=messages,
    )
    msg = response.choices[0].message

    # Track API cost
    if response.usage:
        cost = _cost_tracker.track_call(
            model="gpt-4o-mini",
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )
        logger.debug(f"API Cost: {cost}")

    tool_results = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": f"Tool {tc.function.name} executed successfully",
            })

    return msg, tool_results


# ─── Intro ───────────────────────────────────────────────────────────────────

async def generate_intro(
    deck: Deck,
    conversation_history: list[dict],
    cancel_event: asyncio.Event,
):
    """Yields AgentResult for the greeting + move to slide 0."""
    if deck.total == 0:
        result = AgentResult()
        result.spoken_text = "No presentation loaded."
        yield result
        return

    messages = [
        {"role": "system", "content": _intro_system_prompt(deck)},
        {"role": "user", "content": "Start the presentation."},
    ]

    msg, tool_results = await _llm_call(messages, deck, cancel_event)
    if cancel_event.is_set() or msg is None:
        return

    result = AgentResult()

    if msg.tool_calls:
        conversation_history.append(msg.model_dump(exclude_unset=True))
        for tc in msg.tool_calls:
            if tc.function.name == "change_slide":
                args = json.loads(tc.function.arguments)
                idx = max(0, min(int(args["slide_index"]), deck.total - 1))
                result.slide_change = idx
                result.slide_reason = args.get("reason", "Starting presentation")

        if result.slide_change is not None:
            yield result  # navigate first

        if cancel_event.is_set():
            return

        conversation_history.extend(tool_results)

        # Get spoken intro after tool call
        follow_up_messages = [
            {"role": "system", "content": _intro_system_prompt(deck)},
            *conversation_history,
        ]
        msg2, _ = await _llm_call(follow_up_messages, deck, cancel_event, max_tokens=256)
        if cancel_event.is_set() or msg2 is None:
            return
        spoken = (msg2.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})
    else:
        spoken = (msg.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})

    if spoken and not cancel_event.is_set():
        result.spoken_text = spoken
        result.should_continue_presenting = True
        yield result


# ─── Present one slide ────────────────────────────────────────────────────────

async def present_slide(
    slide_index: int,
    deck: Deck,
    conversation_history: list[dict],
    cancel_event: asyncio.Event,
):
    """Present a single slide. Yields AgentResult(s) including next slide change."""
    if cancel_event.is_set():
        return

    messages = [
        {"role": "system", "content": _presenter_system_prompt(deck, slide_index)},
        *conversation_history,
        {"role": "user", "content": f"Present slide {slide_index} now."},
    ]

    msg, tool_results = await _llm_call(messages, deck, cancel_event, max_tokens=512)
    if cancel_event.is_set() or msg is None:
        return

    result = AgentResult()
    next_slide: int | None = None

    if msg.tool_calls:
        conversation_history.append(msg.model_dump(exclude_unset=True))

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            if tc.function.name == "change_slide":
                idx = max(0, min(int(args["slide_index"]), deck.total - 1))
                next_slide = idx
                result.slide_change = idx
                result.slide_reason = args.get("reason", "Next slide")
            elif tc.function.name == "presentation_complete":
                result.presentation_complete = True

        conversation_history.extend(tool_results)

        # Get spoken content after tool execution
        follow_up = [
            {"role": "system", "content": _presenter_system_prompt(deck, slide_index)},
            *conversation_history,
        ]
        msg2, _ = await _llm_call(follow_up, deck, cancel_event, max_tokens=512)
        if cancel_event.is_set() or msg2 is None:
            return

        spoken = (msg2.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})
    else:
        spoken = (msg.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})

    if spoken and not cancel_event.is_set():
        result.spoken_text = spoken

    # Signal to keep auto-advancing if there are more slides
    if not result.presentation_complete and next_slide is not None:
        result.should_continue_presenting = True
    elif not result.presentation_complete and next_slide is None:
        # Agent forgot to call change_slide — auto-advance
        next_idx = slide_index + 1
        if next_idx < deck.total:
            result.slide_change = next_idx
            result.slide_reason = "Auto-advance"
            result.should_continue_presenting = True

    yield result


# ─── Answer doubt ─────────────────────────────────────────────────────────────

async def answer_doubt(
    question: str,
    current_slide: int,
    resume_slide: int,
    deck: Deck,
    conversation_history: list[dict],
    cancel_event: asyncio.Event,
):
    """Answer a user question and signal to resume presenting from resume_slide."""
    conversation_history.append({"role": "user", "content": question})

    messages = [
        {"role": "system", "content": _doubt_system_prompt(deck, current_slide, resume_slide)},
        *conversation_history,
    ]

    msg, tool_results = await _llm_call(messages, deck, cancel_event, max_tokens=512)
    if cancel_event.is_set() or msg is None:
        return

    result = AgentResult()

    if msg.tool_calls:
        conversation_history.append(msg.model_dump(exclude_unset=True))
        for tc in msg.tool_calls:
            if tc.function.name == "change_slide":
                args = json.loads(tc.function.arguments)
                idx = max(0, min(int(args["slide_index"]), deck.total - 1))
                result.slide_change = idx
                result.slide_reason = args.get("reason", "Answering doubt")

        if result.slide_change is not None:
            yield result

        if cancel_event.is_set():
            return

        conversation_history.extend(tool_results)
        follow_up = [
            {"role": "system", "content": _doubt_system_prompt(deck, current_slide, resume_slide)},
            *conversation_history,
        ]
        msg2, _ = await _llm_call(follow_up, deck, cancel_event, max_tokens=512)
        if cancel_event.is_set() or msg2 is None:
            return
        spoken = (msg2.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})
    else:
        spoken = (msg.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})

    if spoken and not cancel_event.is_set():
        result.spoken_text = spoken
        result.should_continue_presenting = True  # resume after answering
    yield result


# ─── Legacy: process_user_message (kept for non-presentation Q&A) ────────────

async def process_user_message(
    text: str,
    current_slide: int,
    deck: Deck,
    conversation_history: list[dict],
    cancel_event: asyncio.Event,
):
    if deck.total == 0:
        result = AgentResult()
        result.spoken_text = "No presentation loaded. Please upload a deck first."
        yield result
        return

    conversation_history.append({"role": "user", "content": text})

    messages = [
        {"role": "system", "content": _presenter_system_prompt(deck, current_slide)},
        *conversation_history,
    ]

    msg, tool_results = await _llm_call(messages, deck, cancel_event)
    if cancel_event.is_set() or msg is None:
        return

    result = AgentResult()

    if msg.tool_calls:
        conversation_history.append(msg.model_dump(exclude_unset=True))
        for tc in msg.tool_calls:
            if tc.function.name == "change_slide":
                args = json.loads(tc.function.arguments)
                idx = max(0, min(int(args["slide_index"]), deck.total - 1))
                result.slide_change = idx
                result.slide_reason = args.get("reason", "")

        if result.slide_change is not None:
            yield result

        if cancel_event.is_set():
            return

        conversation_history.extend(tool_results)
        follow_up = [
            {"role": "system", "content": _presenter_system_prompt(deck, result.slide_change or current_slide)},
            *conversation_history,
        ]
        msg2, _ = await _llm_call(follow_up, deck, cancel_event, max_tokens=512)
        if cancel_event.is_set() or msg2 is None:
            return
        spoken = (msg2.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})
    else:
        spoken = (msg.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})

    if spoken and not cancel_event.is_set():
        result.spoken_text = spoken
        yield result


# ─── Cost tracking utilities ──────────────────────────────────────────────────

def get_session_cost_summary() -> dict:
    """Get the cost summary for the current session."""
    return _cost_tracker.get_summary()


def log_session_cost_summary():
    """Log a summary of all costs in the session."""
    _cost_tracker.log_summary()


def reset_session_costs():
    """Reset the cost tracker for a new session."""
    _cost_tracker.reset()
    logger.info("Cost tracker reset for new session")
