from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Slide:
    id: int
    title: str
    subtitle: str
    bullets: list[str]
    notes: str = ""


@dataclass
class Deck:
    """Holds parsed slides for a session."""
    slides: list[Slide] = field(default_factory=list)

    def get_agent_context(self) -> str:
        """Concise text representation injected into the agent system prompt."""
        lines = []
        for s in self.slides:
            lines.append(f"[Slide {s.id}] {s.title}")
            if s.subtitle:
                lines.append(f"  Subtitle: {s.subtitle}")
            for b in s.bullets:
                lines.append(f"  • {b}")
            if s.notes:
                lines.append(f"  Speaker notes: {s.notes}")
        return "\n".join(lines)

    @property
    def total(self) -> int:
        return len(self.slides)