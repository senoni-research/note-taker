"""Entry point for `note-taker-asr` console script."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "start_asr.sh"
    if not script.exists():
        print(f"Missing {script}", file=sys.stderr)
        sys.exit(1)
    os.execv(str(script), [str(script), *sys.argv[1:]])


if __name__ == "__main__":
    main()
