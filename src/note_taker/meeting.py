"""Live meeting: mic → SecurePcmRing → WhisperLiveKit → text-only persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import sys
import uuid
from datetime import UTC, datetime

import numpy as np
import sounddevice as sd

from note_taker.asr import AsrUpdate, TranscriptLine, WhisperLiveKitSession
from note_taker.notes import generate_notes
from note_taker.paths import meetings_dir
from note_taker.pcm_ring import SecurePcmRing

log = logging.getLogger(__name__)

# WhisperLiveKit only closes a line on VAD silence, so continuous speech arrives
# as one ever-growing line. Split it on sentence terminators for usable evidence
# granularity. A terminator followed by whitespace avoids splitting "3.5".
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_BOUNDARY.split(text.strip()) if part]


def _stamp(ms: int) -> str:
    hours, rem = divmod(max(0, ms) // 1000, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def render_transcript_text(segments: list[dict]) -> str:
    """Readable transcript, for when the transcript itself is the deliverable."""
    return "".join(f"[{_stamp(seg['start_ms'])}] {seg['text']}\n" for seg in segments)


class TranscriptArchive:
    """WLK server-window + upserting archive (one segment per sentence)."""

    def __init__(self, meeting_id: str) -> None:
        self.meeting_id = meeting_id
        self.server_window: list[TranscriptLine] = []
        self.segments: list[dict] = []
        self.provisional = ""
        self.epoch = 0
        self.out_of_sync = False
        self._by_id: dict[str, dict] = {}
        self._line_segment_ids: dict[str, list[str]] = {}

    def apply(self, update: AsrUpdate) -> list[tuple[str, dict]]:
        """Apply an ASR update.

        Returns list of (event, segment) where event is ``new`` or ``update``.
        """
        events: list[tuple[str, dict]] = []

        if update.kind == "snapshot":
            self.server_window = list(update.new_lines)
            for line in self.server_window:
                events.extend(self._upsert(line))
            self.provisional = update.buffer_transcription
            self.out_of_sync = False
            return events

        if update.kind == "diff":
            if update.lines_pruned:
                self.server_window = self.server_window[update.lines_pruned :]

            if update.new_lines:
                if update.n_lines is not None:
                    keep = max(0, update.n_lines - len(update.new_lines))
                    self.server_window = self.server_window[:keep]
                self.server_window.extend(update.new_lines)
                for line in update.new_lines:
                    events.extend(self._upsert(line))

            if (
                update.n_lines is not None
                and len(self.server_window) != update.n_lines
            ):
                if not self.out_of_sync:
                    log.debug(
                        "ASR n_lines mismatch: window=%s n_lines=%s",
                        len(self.server_window),
                        update.n_lines,
                    )
                self.out_of_sync = True
            else:
                self.out_of_sync = False

            self.provisional = update.buffer_transcription
            return events

        if update.buffer_transcription:
            self.provisional = update.buffer_transcription
        return events

    def _line_key(self, line: TranscriptLine) -> str:
        # Stable across in-place revisions of the same utterance.
        return f"{round((line.start or 0.0) * 100):08d}"

    def _upsert(self, line: TranscriptLine) -> list[tuple[str, dict]]:
        text = " ".join((line.text or "").split())
        if not text:
            return []

        line_key = self._line_key(line)
        line_start = line.start or 0.0
        line_end = max(line.end or line_start, line_start)
        sentences = split_sentences(text)
        # WLK reports timings per line only, so sentence boundaries are placed
        # proportionally by character offset within the line's span.
        total_chars = sum(len(s) for s in sentences) or 1
        span = line_end - line_start
        now = datetime.now(UTC).isoformat()

        events: list[tuple[str, dict]] = []
        seen_ids: list[str] = []
        consumed = 0

        for index, sentence in enumerate(sentences):
            seg_id = f"seg-{line_key}-{index:02d}"
            seen_ids.append(seg_id)
            start = line_start + span * (consumed / total_chars)
            consumed += len(sentence)
            end = line_start + span * (consumed / total_chars)
            event = self._upsert_sentence(seg_id, sentence, start, end, now)
            if event:
                events.append(event)

        self._drop_stale_sentences(line_key, seen_ids)
        self._line_segment_ids[line_key] = seen_ids
        return events

    def _upsert_sentence(
        self, seg_id: str, text: str, start: float, end: float, now: str
    ) -> tuple[str, dict] | None:
        existing = self._by_id.get(seg_id)
        if existing is not None:
            text_changed = existing["text"] != text
            # Re-interpolate both bounds against the line's current span, or
            # sentences would overlap: the span grows after a start is first set.
            existing["start_ms"] = int(start * 1000)
            existing["end_ms"] = int(end * 1000)
            existing["committed_at"] = now
            if not text_changed:
                return None
            existing["text"] = text
            return ("update", existing)

        seg = {
            "id": seg_id,
            "meeting_id": self.meeting_id,
            "source": "you",
            "start_ms": int(start * 1000),
            "end_ms": int(end * 1000),
            "text": text,
            "asr_epoch": self.epoch,
            "committed_at": now,
        }
        self._by_id[seg_id] = seg
        self.segments.append(seg)
        return ("new", seg)

    def _drop_stale_sentences(self, line_key: str, seen_ids: list[str]) -> None:
        """Remove segments left behind when a revision merges sentences."""
        stale = set(self._line_segment_ids.get(line_key, [])) - set(seen_ids)
        if not stale:
            return
        for seg_id in stale:
            self._by_id.pop(seg_id, None)
        self.segments = [seg for seg in self.segments if seg["id"] not in stale]


async def run_meeting(
    *,
    asr_ws_url: str,
    language: str,
    ollama_url: str,
    ollama_model: str,
    sample_rate: int = 16000,
    ring_seconds: float = 45.0,
    title: str | None = None,
    make_notes: bool = True,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    meeting_id = str(uuid.uuid4())
    meeting_path = meetings_dir() / meeting_id
    meeting_path.mkdir(parents=True, exist_ok=True)
    transcript_path = meeting_path / "transcript.json"
    transcript_text_path = meeting_path / "transcript.txt"

    stop_hint = "stop and generate notes" if make_notes else "stop"
    print(
        "Audio in memory only — no audio file is being created.\n"
        f"Meeting id: {meeting_id}\n"
        f"Speak into the microphone. Press Ctrl+C to {stop_hint}.\n"
    )

    capacity = int(sample_rate * ring_seconds)
    ring = SecurePcmRing(capacity)
    archive = TranscriptArchive(meeting_id)
    stop_event = asyncio.Event()
    ready_to_stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    session = WhisperLiveKitSession(asr_ws_url, language=language)
    await session.connect()

    block = max(1, int(sample_rate * 0.02))
    send_samples = max(block, int(sample_rate * 0.25))

    def audio_callback(indata, frames, time_info, status) -> None:
        if status:
            log.debug("PortAudio status: %s", status)
        mono = indata[:, 0] if indata.ndim > 1 else indata
        clipped = np.clip(mono, -1.0, 1.0)
        pcm = np.ascontiguousarray((clipped * 32767.0).astype("<i2"))
        ring.push(pcm.tobytes())

    stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=block,
        callback=audio_callback,
    )

    def persist_transcript() -> None:
        transcript_path.write_text(
            json.dumps(archive.segments, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # Written continuously so a hard kill still leaves a usable transcript.
        transcript_text_path.write_text(
            render_transcript_text(archive.segments), encoding="utf-8"
        )

    async def sender() -> None:
        while not stop_event.is_set():
            chunk = ring.pop_exact(send_samples)
            if chunk is None:
                await asyncio.sleep(0.05)
                continue
            await session.push_pcm(chunk)
        while True:
            leftover = min(len(ring), send_samples)
            if leftover <= 0:
                break
            chunk = ring.pop_exact(leftover)
            if not chunk:
                break
            await session.push_pcm(chunk)

    def _show_provisional(seg_id: str, text: str) -> None:
        # Cursor's terminal log ignores carriage returns, so emit a bounded
        # preview rather than printing the entire growing utterance each tick.
        preview = text if len(text) <= 180 else f"…{text[-179:]}"
        print(f"… [{seg_id}] {preview}", flush=True)

    def _finalize_open(seg_id: str | None) -> None:
        if not seg_id or seg_id not in archive._by_id:
            return
        seg = archive._by_id[seg_id]
        print(f"✓ [{seg['id']}] {seg['text']}", flush=True)

    async def receiver() -> None:
        open_seg_id: str | None = None
        last_shown_text: str | None = None
        last_buffer = ""
        last_preview_at = 0.0
        async for update in session.updates():
            events = archive.apply(update)
            if events:
                persist_transcript()
            for event, seg in events:
                if event == "new":
                    # A following sentence exists, so the previous one is settled.
                    if open_seg_id and open_seg_id != seg["id"]:
                        _finalize_open(open_seg_id)
                    open_seg_id = seg["id"]
                    last_shown_text = seg["text"]
                    _show_provisional(seg["id"], seg["text"])
                    last_preview_at = loop.time()
                elif seg["id"] == open_seg_id:
                    if seg["text"] != last_shown_text:
                        last_shown_text = seg["text"]
                        if loop.time() - last_preview_at >= 2.0:
                            _show_provisional(seg["id"], seg["text"])
                            last_preview_at = loop.time()
                else:
                    # Whisper revised a sentence already shown as committed.
                    print(f"↻ [{seg['id']}] {seg['text']}", flush=True)
            buf = (update.buffer_transcription or "").strip()
            if (
                buf
                and buf != last_buffer
                and not events
                and loop.time() - last_preview_at >= 2.0
            ):
                last_buffer = buf
                preview = buf if len(buf) <= 180 else f"…{buf[-179:]}"
                print(f"… (provisional) {preview}", flush=True)
                last_preview_at = loop.time()
            if update.kind == "ready_to_stop":
                ready_to_stop.set()
                break

        _finalize_open(open_seg_id)

    stream.start()
    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())

    try:
        await stop_event.wait()
    finally:
        print("\nStopping capture…")
        stream.stop()
        stream.close()
        stop_event.set()
        try:
            await asyncio.wait_for(sender_task, timeout=5.0)
        except TimeoutError:
            sender_task.cancel()
        except Exception as exc:  # noqa: BLE001 - cleanup must continue
            log.warning("Audio sender stopped with an error: %s", exc)
            sender_task.cancel()
        ring.clear()
        try:
            await session.send_end_of_audio()
            await asyncio.wait_for(ready_to_stop.wait(), timeout=15.0)
        except TimeoutError:
            log.warning("Timed out waiting for ASR ready_to_stop")
        await session.close()
        if not receiver_task.done():
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
        persist_transcript()

    meta = {
        "meeting_id": meeting_id,
        "title": title,
        "created_at": datetime.now(UTC).isoformat(),
        "language": language,
        "privacy": "memory-only",
        "audio_file": None,
        "segment_count": len(archive.segments),
        "dropped_pcm_samples": ring.dropped_samples,
    }
    (meeting_path / "metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    if not archive.segments:
        print("No committed transcript segments.")
        print(
            "Tip: speak a full sentence and pause briefly so Whisper can commit a line."
        )
        return

    if not make_notes:
        print(f"\nTranscript: {transcript_text_path}")
        print(f"Segments:   {transcript_path} ({len(archive.segments)})")
        print("PCM buffers wiped — no audio file was created.")
        return

    print(f"Generating notes with Ollama model {ollama_model!r}…")
    try:
        notes = await generate_notes(
            ollama_url=ollama_url,
            model=ollama_model,
            segments=archive.segments,
            meeting_date=datetime.now().astimezone().date(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Note generation failed: {exc}", file=sys.stderr)
        print(f"Transcript saved at {transcript_text_path}")
        return

    notes_path = meeting_path / "notes.json"
    notes_path.write_text(notes.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nTitle: {notes.title}")
    print(f"Summary: {notes.executive_summary}")
    for item in notes.action_items:
        owner = item.owner or "unassigned"
        due = item.due_date or item.due_date_raw or "no due date"
        print(f"Action: {item.task} — {owner} — {due}")
    print(f"Saved text-only artefacts under {meeting_path}")
    print("PCM buffers wiped — no audio file was created.")
