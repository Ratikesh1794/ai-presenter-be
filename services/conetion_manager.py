from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from fastapi import WebSocket

from models.slides import Deck


class PresentationMode(Enum):
    IDLE = "idle"
    PRESENTING = "presenting"
    ANSWERING_DOUBT = "answering_doubt"
    COMPLETE = "complete"


@dataclass
class ConnectionState:
    websocket: WebSocket
    current_slide: int = 0
    conversation_history: list[dict] = field(default_factory=list)
    deck: Deck = field(default_factory=Deck)

    # Presentation state machine
    mode: PresentationMode = PresentationMode.IDLE
    resume_slide: int = 0          # slide to return to after answering a doubt
    presentation_started: bool = False

    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    agent_task: asyncio.Task | None = None

    def interrupt(self) -> None:
        self.cancel_event.set()
        if self.agent_task and not self.agent_task.done():
            self.agent_task.cancel()

    def reset_cancel(self) -> None:
        self.cancel_event = asyncio.Event()
        self.agent_task = None

    def load_deck(self, deck: Deck) -> None:
        self.deck = deck
        self.current_slide = 0
        self.conversation_history = []
        self.mode = PresentationMode.IDLE
        self.resume_slide = 0
        self.presentation_started = False
        self.interrupt()
        self.reset_cancel()


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, ConnectionState] = {}

    async def connect(self, websocket: WebSocket) -> ConnectionState:
        await websocket.accept()
        state = ConnectionState(websocket=websocket)
        self._connections[str(id(websocket))] = state
        return state

    def disconnect(self, websocket: WebSocket) -> None:
        state = self._connections.pop(str(id(websocket)), None)
        if state:
            state.interrupt()

    def get(self, websocket: WebSocket) -> ConnectionState | None:
        return self._connections.get(str(id(websocket)))


manager = ConnectionManager()