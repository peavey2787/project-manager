from __future__ import annotations
import re
from .models import ChatState

ERROR_RE = re.compile(
    r"(message delivery timed out\.?\s*please try again\.?|there was an error|something went wrong|network error|error generating|failed to generate|"
    r"try again later|unable to load|rate limit|you.ve reached)",
    re.IGNORECASE,
)


def classify_detected_state(snapshot, prior_state: ChatState = ChatState.IDLE) -> ChatState:
    """Classify a fresh accessibility scan into the four user-facing states."""
    if snapshot is None or not getattr(snapshot, "available", False):
        return ChatState.ERROR
    if str(getattr(snapshot, "error_text", "") or "").strip():
        return ChatState.ERROR
    composer_state = str(getattr(snapshot, "composer_state", "") or "").casefold()
    if bool(getattr(snapshot, "in_progress", False)) or composer_state == "stop":
        return ChatState.IN_PROGRESS
    if bool(getattr(snapshot, "latest_response_complete", False)):
        return ChatState.SUCCESS
    if composer_state in {"send", "voice"}:
        if prior_state == ChatState.SUCCESS or bool(getattr(snapshot, "has_assistant_response", False)):
            return ChatState.SUCCESS
        return ChatState.IDLE
    return ChatState.IDLE


def is_message_delivery_timeout(value: str) -> bool:
    text = str(value or "").strip().casefold()
    return "message delivery timed out" in text and "please try again" in text
