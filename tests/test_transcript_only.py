"""Transcript-only runs must produce files without ever reaching Ollama."""

from __future__ import annotations

import asyncio
import json
import os
import signal

import pytest

from note_taker import meeting as meeting_mod
from note_taker.asr import parse_asr_message

SNAPSHOT = {
    "type": "snapshot",
    "seq": 1,
    "lines": [
        {
            "speaker": 1,
            "text": "Let us ship the transcript only mode. Priya will update the glossary.",
            "start": "0:00:01.00",
            "end": "0:00:06.00",
        }
    ],
    "buffer_transcription": "",
}


class FakeStream:
    """Stands in for sounddevice; the fake ASR session supplies the text."""

    def __init__(self, **_kwargs) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        pass


class FakeSession:
    def __init__(self, *_args, **_kwargs) -> None:
        self.ended = False
        self.closed = False

    async def connect(self) -> None:
        pass

    async def push_pcm(self, _chunk: bytes) -> None:
        pass

    async def send_end_of_audio(self) -> None:
        self.ended = True

    async def close(self) -> None:
        self.closed = True

    async def updates(self):
        yield parse_asr_message(SNAPSHOT)
        yield parse_asr_message({"type": "ready_to_stop"})


async def _stop_once_running(path) -> None:
    for _ in range(200):
        if path.exists():
            os.kill(os.getpid(), signal.SIGINT)
            return
        await asyncio.sleep(0.02)
    raise AssertionError("transcript was never written")


async def _drive(meeting_root) -> None:
    watcher = asyncio.create_task(_stop_once_running(meeting_root))
    await asyncio.wait_for(
        meeting_mod.run_meeting(
            asr_ws_url="ws://127.0.0.1:8000/asr",
            language="en",
            ollama_url="http://127.0.0.1:11434",
            ollama_model="unused",
            make_notes=False,
        ),
        timeout=20,
    )
    await watcher


def test_transcript_only_writes_files_and_skips_notes(tmp_path, monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise AssertionError("transcript-only must not call Ollama")

    monkeypatch.setattr(meeting_mod.sd, "InputStream", FakeStream)
    monkeypatch.setattr(meeting_mod, "WhisperLiveKitSession", FakeSession)
    monkeypatch.setattr(meeting_mod, "generate_notes", boom)
    monkeypatch.setattr(meeting_mod, "meetings_dir", lambda: tmp_path)

    # The transcript file lands in the single meeting directory created by the run.
    asyncio.run(_drive(tmp_path))

    meetings = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(meetings) == 1
    meeting_path = meetings[0]

    text = (meeting_path / "transcript.txt").read_text(encoding="utf-8")
    assert text == (
        "[00:01] Let us ship the transcript only mode.\n"
        "[00:03] Priya will update the glossary.\n"
    )

    segments = json.loads((meeting_path / "transcript.json").read_text(encoding="utf-8"))
    assert [seg["text"] for seg in segments] == [
        "Let us ship the transcript only mode.",
        "Priya will update the glossary.",
    ]

    meta = json.loads((meeting_path / "metadata.json").read_text(encoding="utf-8"))
    assert meta["audio_file"] is None
    assert meta["segment_count"] == 2

    assert not (meeting_path / "notes.json").exists()
    assert "Generating notes" not in capsys.readouterr().out


@pytest.mark.parametrize("ms,expected", [(0, "00:00"), (62_500, "01:02")])
def test_stamp_formats(ms, expected):
    assert meeting_mod._stamp(ms) == expected
