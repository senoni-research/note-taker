# WhisperLiveKit ASR protocol notes

This document mixes **implemented behaviour** with a **target architecture**.
See [Implementation status](#implementation-status) before treating every rule as
shipping code.

Default local server:

```bash
source .venv/bin/activate
wlk --host 127.0.0.1 --port 8000 --pcm-input --model small --language en
```

Health: `GET http://127.0.0.1:8000/health`  
WebSocket: `ws://127.0.0.1:8000/asr?language=<lang>&mode=diff`

## Audio format

Send binary frames of signed 16-bit little-endian PCM, 16 kHz, mono. Start the server with `--pcm-input` so the live path does not expect encoded media.

## Diff vs snapshot

WhisperLiveKit distinguishes:

- **Committed lines** — stable text the client archives once
- **Provisional buffer** — volatile text the UI **replaces**, never appends
- **`lines_pruned`** — removes lines from the *server window* only; never delete already-archived segments

### Client rules

1. Maintain `server_window` (current WLK window) and `archive` (persisted segments).
2. On snapshot: replace `server_window`; archive only lines not already committed.
3. On diff: verify `seq`, apply prune + append, check `n_lines`, archive new committed lines once, replace provisional fields wholesale.
4. On seq / `n_lines` mismatch: mark out-of-sync, reconnect (new ASR epoch), reconcile first snapshot against archive tail.
5. Segment IDs must not rely solely on WebSocket sequence numbers (they reset on reconnect). Prefer meeting id + source + epoch + time + normalised text hash.

## Line vs segment granularity

WhisperLiveKit closes a line only when its VAD reports silence, so a continuous
reading with short breaths arrives as a single line that keeps growing for
minutes. That is too coarse for evidence citations and for a live display.

The client therefore splits each committed line on sentence terminators and
stores one segment per sentence, with id `seg-<line-start-centiseconds>-<index>`:

- Line start time keys the id, so in-place revisions of the same utterance update
  segments instead of duplicating them.
- Sentence start/end times are **interpolated** by character offset inside the
  line's span, because WLK reports timings per line only. Treat them as
  approximate positions, not word-accurate alignments.
- When a revision merges sentences (punctuation disappears), segments left beyond
  the new sentence count are dropped so the archive never keeps a stale tail.

## Stop / abort

- **Stop:** drain bounded send queue → empty binary frame → wait for `ready_to_stop` (with timeout) → wipe PCM → keep committed text.
- **Abort:** wipe pending PCM → close socket → persist no provisional text.

## Implementation status

| Rule / behaviour | Python CLI | Meetily Rust client |
|---|---|---|
| Loopback-only connect + require `--pcm-input` | Implemented | Implemented |
| Binary PCM frames (16 kHz mono s16le) | Implemented | Implemented |
| Diff / snapshot parse; archive committed lines | Implemented | Implemented |
| Sentence-level upsert (`seg-<line-start-cs>-<index>`) | Implemented | Implemented |
| Check `n_lines`; mark archive out-of-sync on mismatch | Implemented | Implemented |
| Show / replace provisional buffer in the live UI | Implemented (terminal) | **Not forwarded** — `AsrEvent::Provisional` is dropped; UI updates come from committed/revised sentences only |
| Parse `seq` from server messages | Parsed into `AsrUpdate` | **Not carried** on `AsrUpdate` |
| Verify every `seq`; treat gaps as out-of-sync | **Specified, not implemented** | **Specified, not implemented** |
| Reconnect + new ASR epoch + reconcile snapshot vs archive tail | Epoch field exists; full reconnect/reconcile **not implemented** | Out-of-sync flag only; reconnect/reconcile **not implemented** |
| Segment identity = meeting + source + epoch + time + text hash | **Specified**; ids are line-start + sentence index today | Same as CLI (line-start + sentence index) |
| Bounded PCM send queue on stop / abort wipe | Implemented (ring + wipe) | PCM path exists; desktop mixed-audio / event channels are still **unbounded** `mpsc` (separate hardening item) |

When this document describes seq verification, epoch reconnect, or hash-based
segment identity, that is the **target** protocol unless the table above marks
it implemented.
