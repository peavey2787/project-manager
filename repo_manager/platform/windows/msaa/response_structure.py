from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import MsaaElement

_ASSISTANT_MARKER_RE = re.compile(
    r"^(?:chatgpt said:?|assistant said:?|assistant response:?|chatgpt response:?)$",
    re.IGNORECASE,
)
_WORKED_FOR_RE = re.compile(r"^worked for\s+(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class LatestResponseStructure:
    marker_index: int = -1
    response_actions_index: int = -1
    worked_for_text: str = ""
    has_response_actions: bool = False
    has_copy_response: bool = False
    has_start_voice: bool = False

    @property
    def completed_successfully(self) -> bool:
        return bool(
            self.marker_index >= 0
            and self.worked_for_text
            and self.has_response_actions
            and self.has_copy_response
            and self.has_start_voice
        )


def is_assistant_marker(name: str) -> bool:
    return bool(_ASSISTANT_MARKER_RE.match(str(name or "").strip()))


def analyze_latest_response(elements: Iterable[MsaaElement]) -> LatestResponseStructure:
    rows = list(elements)
    markers = [item.index for item in rows if is_assistant_marker(item.name)]
    if not markers:
        return LatestResponseStructure()

    marker = max(markers)
    after = [item for item in rows if item.index > marker]
    action_rows = [item for item in after if item.name.strip().casefold() == "response actions"]
    response_actions_index = action_rows[0].index if action_rows else -1

    worked_for_text = ""
    has_copy_response = False
    has_start_voice = False
    for item in after:
        name = item.name.strip()
        lower = name.casefold()
        matched = _WORKED_FOR_RE.match(name)
        if matched:
            worked_for_text = matched.group(1).strip()
        elif lower == "copy response":
            has_copy_response = True
        elif lower == "start voice":
            has_start_voice = True

    return LatestResponseStructure(
        marker_index=marker,
        response_actions_index=response_actions_index,
        worked_for_text=worked_for_text,
        has_response_actions=bool(action_rows),
        has_copy_response=has_copy_response,
        has_start_voice=has_start_voice,
    )


__all__ = [
    "LatestResponseStructure",
    "analyze_latest_response",
    "is_assistant_marker",
]
