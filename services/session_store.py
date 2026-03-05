from __future__ import annotations

from models.slides import Deck


class SessionStore:
    """
    In-memory store mapping session_id → Deck.
    Simple and sufficient for a prototype — swap for Redis in production.
    """

    def __init__(self) -> None:
        self._store: dict[str, Deck] = {}

    def save(self, session_id: str, deck: Deck) -> None:
        self._store[session_id] = deck

    def get(self, session_id: str) -> Deck | None:
        return self._store.get(session_id)

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)


session_store = SessionStore()