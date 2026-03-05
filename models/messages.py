from typing import Literal, Union
from pydantic import BaseModel


# ─── Client → Server ──────────────────────────────────────────────────────────

class UserSpeechMessage(BaseModel):
    type: Literal["user_speech"]
    text: str


class InterruptMessage(BaseModel):
    type: Literal["interrupt"]


class SlideChangedMessage(BaseModel):
    type: Literal["slide_changed"]
    index: int


ClientMessage = Union[UserSpeechMessage, InterruptMessage, SlideChangedMessage]


# ─── Server → Client ──────────────────────────────────────────────────────────

class ChangeSlideMessage(BaseModel):
    type: Literal["change_slide"] = "change_slide"
    index: int
    reason: str


class SpeakMessage(BaseModel):
    type: Literal["speak"] = "speak"
    text: str


class StatusMessage(BaseModel):
    type: Literal["status"] = "status"
    state: Literal["idle", "listening", "thinking", "speaking"]


class InterruptedMessage(BaseModel):
    type: Literal["interrupted"] = "interrupted"


ServerMessage = Union[ChangeSlideMessage, SpeakMessage, StatusMessage, InterruptedMessage]