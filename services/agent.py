from __future__ import annotations

import asyncio
import json
import logging
import os

from openai import AsyncOpenAI

from models.slides import Deck

logger = logging.getLogger(__name__)
_client = AsyncOpenAI(api_key=os.environ["LLM_API_KEY"])

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
    return f"""You are an enthusiastic AI presenter delivering a live presentation.
You are currently on slide {current_slide} of {deck.total - 1} (zero-indexed).

SLIDES:
{deck.get_agent_context()}

PRESENTATION MODE RULES:
- You are in AUTO-PRESENTING mode. Present each slide thoroughly in 3-5 sentences.
- After finishing a slide, ALWAYS call change_slide to move to the next one, then continue speaking.
- When you have presented ALL slides ({deck.total} total), call presentation_complete.
- Keep energy high. Speak naturally, no bullet points, no markdown.
- Never ask the user if they are ready — just keep presenting.

CURRENT STATE: Presenting slide {current_slide}. Continue from here."""


def _doubt_system_prompt(deck: Deck, current_slide: int, resume_slide: int) -> str:
    return f"""You are an AI presenter. A user interrupted your presentation on slide {current_slide} with a question.
You are currently on slide {current_slide} of {deck.total - 1}.

SLIDES:
{deck.get_agent_context()}

DOUBT-ANSWERING RULES:
- Answer the user's question clearly and concisely (2-4 sentences).
- If the answer relates to a different slide, call change_slide to navigate there, answer, then navigate BACK to slide {resume_slide}.
- After answering, end with a natural transition like "Now, let me continue where we left off..." 
- Then call change_slide to go back to slide {resume_slide} if not already there.
- Keep the answer focused — don't re-present the whole slide."""


def _intro_system_prompt(deck: Deck) -> str:
    return f"""You are an enthusiastic AI presenter about to start a live presentation.

SLIDES:
{deck.get_agent_context()}

YOUR TASK:
- Greet the audience warmly. Start with "Hello! Today's session is about..."
- Give a brief 2-3 sentence overview of what will be covered across all {deck.total} slides.
- End with "Let's get started!" then immediately call change_slide to navigate to slide 0 and begin.
- Do NOT wait for any response. Just introduce and start.
- Keep it under 4 sentences total."""


# ─── Agent result ─────────────────────────────────────────────────────────────

class AgentResult:
    def __init__(self) -> None:
        self.slide_change: int | None = None
        self.slide_reason: str = ""
        self.spoken_text: str = ""
        self.presentation_complete: bool = False
        self.should_continue_presenting: bool = False  # signal to keep auto-advancing


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
        model="gpt-4o",
        max_tokens=max_tokens,
        tools=_build_tools(deck),
        tool_choice="auto",
        messages=messages,
    )
    msg = response.choices[0].message

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