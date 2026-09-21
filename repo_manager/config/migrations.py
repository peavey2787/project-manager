from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 16


def migrate_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize persisted config to the current schema without mutating input."""
    payload = dict(data)
    # Historical schemas were additive. Loading code supplies defaults for any
    # absent fields, so migration only stamps the canonical current version.
    payload["schema_version"] = SCHEMA_VERSION
    return payload
