from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from note_taker.meeting import run_meeting
from note_taker.notes import generate_notes
from note_taker.paths import meetings_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="note-taker",
        description="Memory-only local meeting notetaker (audio never written to disk).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    meet = sub.add_parser("meet", help="Capture mic → WhisperLiveKit → Ollama notes")
    meet.add_argument("--asr-url", default="ws://127.0.0.1:8000/asr")
    meet.add_argument("--language", default="en")
    meet.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    meet.add_argument("--ollama-model", default="qwen3.5:4b")
    meet.add_argument("--sample-rate", type=int, default=16000)
    meet.add_argument("--ring-seconds", type=float, default=45.0)
    meet.add_argument("--title", default=None)

    health = sub.add_parser("health", help="Check ASR and Ollama loopback endpoints")
    health.add_argument("--asr-http", default="http://127.0.0.1:8000")
    health.add_argument("--ollama-url", default="http://127.0.0.1:11434")

    args = parser.parse_args()

    if args.command == "health":
        asyncio.run(_health(args.asr_http, args.ollama_url))
        return

    if args.command == "meet":
        asyncio.run(
            run_meeting(
                asr_ws_url=args.asr_url,
                language=args.language,
                ollama_url=args.ollama_url,
                ollama_model=args.ollama_model,
                sample_rate=args.sample_rate,
                ring_seconds=args.ring_seconds,
                title=args.title,
            )
        )


async def _health(asr_http: str, ollama_url: str) -> None:
    import httpx

    failed = False
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in (
            ("WhisperLiveKit", f"{asr_http.rstrip('/')}/health"),
            ("Ollama", f"{ollama_url.rstrip('/')}/api/tags"),
        ):
            try:
                r = await client.get(url)
                print(f"{name}: OK ({r.status_code}) {url}")
            except Exception as exc:  # noqa: BLE001
                print(f"{name}: FAIL {url} — {exc}", file=sys.stderr)
                failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
