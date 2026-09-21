from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", errors="replace", buffering=1) as handle:
        for line in sys.stdin:
            sys.stdout.write(line)
            sys.stdout.flush()
            handle.write(line)
            handle.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
