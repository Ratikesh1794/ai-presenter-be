from __future__ import annotations

import asyncio
import json
import os

from openai import AsyncOpenAI

from models.slides import Deck

_client = AsyncOpenAI(api_key=os.environ["LLM_API_KEY"])

# ─── Tools ────────────────────────────────────────────────────────────────────

def _build_tools(deck: Deck) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "change_slide",
                "description": (
                    "Navigate the presentation to a specific slide. "
                    "Call this when the user's question is best answered by a different slide, "
                    "or when they explicitly ask to move to one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slide_index": {
                            "type": "integer",
                            "description": f"Zero-based slide index (0–{deck.total - 1})",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason why this slide is most relevant",
                        },
                    },
                    "required": ["slide_index", "reason"],
                },
            },
        }
    ]


# ─── System prompt ────────────────────────────────────────────────────────────

def _system_prompt(deck: Deck, current_slide: int) -> str:
    return f"""You are an AI presenter delivering a presentation. 
You are currently on slide {current_slide} of {deck.total - 1}.

SLIDES IN THIS DECK:
{deck.get_agent_context()}

YOUR RESPONSIBILITIES:
1. Answer the user's question conversationally and concisely (2–4 sentences max).
2. If their question is better addressed by a different slide, call the `change_slide` tool BEFORE speaking.
3. You can change slide AND speak in the same turn — tool first, then text.
4. Base your answers on the slide content and speaker notes above.
5. Keep responses short — this is voice output, not an essay.
6. Never mention slide numbers to the user; navigate invisibly.

TONE: Confident, clear, conversational. No markdown, no bullet points in speech."""


# ─── Agent result ─────────────────────────────────────────────────────────────

class AgentResult:
    def __init__(self) -> None:
        self.slide_change: int | None = None
        self.slide_reason: str = ""
        self.spoken_text: str = ""


# ─── Agent ────────────────────────────────────────────────────────────────────

async def process_user_message(
    text: str,
    current_slide: int,
    deck: Deck,
    conversation_history: list[dict],
    cancel_event: asyncio.Event,
):
    """
    Yields AgentResult objects:
      1. Slide change (if needed) — yielded first so frontend navigates immediately
      2. Spoken response — yielded once assembled
    """
    if deck.total == 0:
        result = AgentResult()
        result.spoken_text = "No presentation is loaded yet. Please upload a deck first."
        yield result
        return

    conversation_history.append({"role": "user", "content": text})
    result = AgentResult()

    if cancel_event.is_set():
        return

    # ── First call ────────────────────────────────────────────────────────────
    messages = [
        {"role": "system", "content": _system_prompt(deck, current_slide)},
        *conversation_history,
    ]

    response = await _client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1024,
        tools=_build_tools(deck),
        tool_choice="auto",
        messages=messages,
    )

    if cancel_event.is_set():
        return

    assistant_message = response.choices[0].message

    # ── Handle tool calls ─────────────────────────────────────────────────────
    if assistant_message.tool_calls:
        conversation_history.append(assistant_message.model_dump(exclude_unset=True))

        tool_results = []
        for tool_call in assistant_message.tool_calls:
            if tool_call.function.name == "change_slide":
                args = json.loads(tool_call.function.arguments)
                idx = max(0, min(int(args["slide_index"]), deck.total - 1))
                result.slide_change = idx
                result.slide_reason = args.get("reason", "")
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Slide changed to {idx}",
                })

        if result.slide_change is not None:
            yield result  # frontend navigates immediately

        if cancel_event.is_set():
            return

        conversation_history.extend(tool_results)

        follow_up = await _client.chat.completions.create(
            model="gpt-4o",
            max_tokens=512,
            messages=[
                {"role": "system", "content": _system_prompt(deck, result.slide_change or current_slide)},
                *conversation_history,
            ],
        )

        if cancel_event.is_set():
            return

        spoken = (follow_up.choices[0].message.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})

    else:
        spoken = (assistant_message.content or "").strip()
        conversation_history.append({"role": "assistant", "content": spoken})

    if spoken and not cancel_event.is_set():
        result.spoken_text = spoken
        yield result