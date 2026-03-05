from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models.slides import Deck

logger = logging.getLogger(__name__)

SLIDES_OUTPUT_DIR = Path(__file__).parent.parent / "static" / "slides"


@dataclass
class Session:
    deck: Deck
    slide_image_urls: list[str] = field(default_factory=list)  # relative URLs


class SessionStore:
    def __init__(self) -> None:
        self._store: dict[str, Session] = {}
        SLIDES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, deck: Deck, image_urls: list[str] | None = None) -> None:
        self._store[session_id] = Session(deck=deck, slide_image_urls=image_urls or [])
        logger.info(f"[SESSION] Saved session {session_id}: {deck.total} slides, {len(image_urls or [])} images")

    def get(self, session_id: str) -> Session | None:
        return self._store.get(session_id)

    def get_deck(self, session_id: str) -> Deck | None:
        session = self._store.get(session_id)
        return session.deck if session else None

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    @property
    def slides_dir(self) -> Path:
        return SLIDES_OUTPUT_DIR


session_store = SessionStore()