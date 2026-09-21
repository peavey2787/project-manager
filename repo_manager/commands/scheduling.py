from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..domain import CommandSpec


@dataclass(frozen=True)
class AutoRunGroup:
    """Commands that share one auto-run order and therefore start together."""

    order: int
    commands: tuple[CommandSpec, ...]


def build_auto_run_groups(commands: Iterable[CommandSpec]) -> list[AutoRunGroup]:
    """Return enabled auto-run commands grouped by order 0..9.

    Commands with the same order preserve their project-list order and are meant
    to start concurrently.  Groups themselves run in ascending numeric order.
    """
    grouped: dict[int, list[CommandSpec]] = {}
    for command in commands:
        if not command.auto_run:
            continue
        order = max(0, min(9, int(command.auto_run_order)))
        grouped.setdefault(order, []).append(command)
    return [AutoRunGroup(order, tuple(grouped[order])) for order in sorted(grouped)]
