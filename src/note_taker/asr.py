"""WhisperLiveKit WebSocket client (PCM in, diff/snapshot out)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import websockets
from websockets.asyncio.client import ClientConnection

from note_taker.endpoints import assert_loopback

log = logging.getLogger(__name__)

# WhisperLiveKit format_time: H:MM:SS.cc (centiseconds)
_TIME_RE = re.compile(
    r"^(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})(?:\.(?P<frac>\d{1,3}))?$"
)


def parse_wlk_time(value: Any) -> float | None:
    """Parse WLK start/end into seconds. Accepts float or 'H:MM:SS.cc'."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    match = _TIME_RE.match(text)
    if not match:
        return None
    h = int(match.group("h"))
    m = int(match.group("m"))
    s = int(match.group("s"))
    frac = match.group("frac") or "0"
    # Centiseconds (2 digits) or milliseconds (3)
    frac_seconds = int(frac) / (100.0 if len(frac) <= 2 else 1000.0)
    return h * 3600 + m * 60 + s + frac_seconds


@dataclass
class TranscriptLine:
    text: str
    start: float | None = None
    end: float | None = None
    speaker: int | str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_silence(self) -> bool:
        return self.speaker == -2 or (
            isinstance(self.speaker, int) and self.speaker == -2
        )


@dataclass
class AsrUpdate:
    kind: str  # config | snapshot | diff | ready_to_stop | error | other
    seq: int | None = None
    n_lines: int | None = None
    lines_pruned: int = 0
    new_lines: list[TranscriptLine] = field(default_factory=list)
    buffer_transcription: str = ""
    lag: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_line(item: Any) -> TranscriptLine:
    if isinstance(item, str):
        return TranscriptLine(text=item)
    if not isinstance(item, dict):
        return TranscriptLine(text=str(item))
    text = item.get("text")
    if text is None:
        text = item.get("utterance") or ""
    return TranscriptLine(
        text=str(text),
        start=parse_wlk_time(item.get("start", item.get("start_time"))),
        end=parse_wlk_time(item.get("end", item.get("end_time"))),
        speaker=item.get("speaker"),
        raw=item,
    )


def parse_asr_message(data: dict[str, Any]) -> AsrUpdate:
    """Typed parse of WhisperLiveKit JSON messages."""
    msg_type = data.get("type")

    if msg_type == "ready_to_stop" or data.get("ready_to_stop") is True:
        return AsrUpdate(kind="ready_to_stop", raw=data)

    if msg_type == "config" or (
        data.get("useAudioWorklet") is not None
        and "lines" not in data
        and msg_type is None
    ):
        return AsrUpdate(kind="config", raw=data)

    if data.get("error") and msg_type == "error":
        return AsrUpdate(kind="error", raw=data)

    buffer = str(data.get("buffer_transcription") or "")
    lag = _as_float(
        data.get("remaining_time_transcription")
        or data.get("lag")
        or data.get("transcription_lag")
    )

    if msg_type == "snapshot" or ("lines" in data and msg_type != "diff"):
        lines = [_parse_line(x) for x in data.get("lines") or []]
        return AsrUpdate(
            kind="snapshot",
            seq=data.get("seq"),
            n_lines=data.get("n_lines", len(lines)),
            new_lines=lines,
            buffer_transcription=buffer,
            lag=lag,
            raw=data,
        )

    if msg_type == "diff" or "new_lines" in data or "lines_pruned" in data:
        new_lines = [_parse_line(x) for x in data.get("new_lines") or []]
        return AsrUpdate(
            kind="diff",
            seq=data.get("seq"),
            n_lines=data.get("n_lines"),
            lines_pruned=int(data.get("lines_pruned") or 0),
            new_lines=new_lines,
            buffer_transcription=buffer,
            lag=lag,
            raw=data,
        )

    return AsrUpdate(kind="other", buffer_transcription=buffer, lag=lag, raw=data)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_asr_ws_url(base: str, language: str, mode: str = "diff") -> str:
    assert_loopback(base)
    parsed = urlparse(base)
    path = parsed.path if parsed.path and parsed.path != "/" else "/asr"
    query = parse_qs(parsed.query)
    query["language"] = [language]
    query["mode"] = [mode]
    flat = [(k, v) for k, vs in query.items() for v in vs]
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, "", urlencode(flat), "")
    )


class WhisperLiveKitSession:
    def __init__(self, ws_url: str, language: str = "en") -> None:
        self.ws_url = build_asr_ws_url(ws_url, language=language)
        self._ws: ClientConnection | None = None
        self._queue: asyncio.Queue[AsrUpdate | None] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    async def connect(self) -> AsrUpdate:
        log.info("Connecting ASR %s", self.ws_url)
        self._ws = await websockets.connect(self.ws_url, max_size=8 * 1024 * 1024)
        raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
        if isinstance(raw, bytes):
            raise RuntimeError("Expected JSON config from WhisperLiveKit, got binary")
        data = json.loads(raw)
        update = parse_asr_message(data)
        if update.kind == "config":
            cfg = update.raw
            mode = cfg.get("mode")
            if mode and mode != "diff":
                log.warning("Server mode is %r; preferring diff", mode)
            if cfg.get("useAudioWorklet") is False:
                raise RuntimeError(
                    "WhisperLiveKit expects encoded media (useAudioWorklet=false). "
                    "Restart the server with --pcm-input."
                )
        self._reader_task = asyncio.create_task(self._read_loop())
        return update

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    log.warning("Non-JSON ASR message: %s", message[:200])
                    continue
                await self._queue.put(parse_asr_message(data))
        except Exception as exc:  # noqa: BLE001
            log.info("ASR reader stopped: %s", exc)
        finally:
            await self._queue.put(None)

    async def push_pcm(self, pcm_s16le: bytes) -> None:
        if not self._ws:
            raise RuntimeError("ASR session not connected")
        if pcm_s16le:
            await self._ws.send(pcm_s16le)

    async def send_end_of_audio(self) -> None:
        """Signal end-of-stream with an empty binary frame (WLK convention)."""
        if self._ws:
            await self._ws.send(b"")

    async def finish(self, timeout: float = 15.0) -> None:
        """Send EOS then wait briefly; receiver should consume ready_to_stop."""
        try:
            await self.send_end_of_audio()
            await asyncio.sleep(min(timeout, 0.5))
        finally:
            await self.close()

    async def abort(self) -> None:
        await self.close()

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def updates(self) -> AsyncIterator[AsrUpdate]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item
