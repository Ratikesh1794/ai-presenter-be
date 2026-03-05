from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from fastapi import WebSocket


@dataclass
class ConnectionState:
    websocket: WebSocket
    current_slide: int = 0
    conversation_history: list[dict] = field(default_factory=list)
    # Set this event to cancel any in-progress agent task
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Reference to the running agent task so we can await/cancel it
    agent_task: asyncio.Task | None = None

    def interrupt(self) -> None:
        """Signal the current agent task to stop and clear for next use."""
        self.cancel_event.set()
        if self.agent_task and not self.agent_task.done():
            self.agent_task.cancel()

    def reset_cancel(self) -> None:
        """Prepare cancel_event for the next request."""
        self.cancel_event = asyncio.Event()
        self.agent_task = None


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, ConnectionState] = {}

    async def connect(self, websocket: WebSocket) -> ConnectionState:
        await websocket.accept()
        conn_id = str(id(websocket))
        state = ConnectionState(websocket=websocket)
        self._connections[conn_id] = state
        return state

    def disconnect(self, websocket: WebSocket) -> None:
        conn_id = str(id(websocket))
        state = self._connections.pop(conn_id, None)
        if state:
            state.interrupt()

    def get(self, websocket: WebSocket) -> ConnectionState | None:
        return self._connections.get(str(id(websocket)))


manager = ConnectionManager()