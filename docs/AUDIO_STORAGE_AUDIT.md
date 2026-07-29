# Audio storage audit (Meetily fork)

Audit date: 2026-07-29  
Base: `Zackriya-Solutions/meetily` @ shallow clone in `meetily/`

## Intent

In the memory-only product, captured audio must never be intentionally written to disk. Only committed transcript text, meeting metadata, and generated notes may persist.

## Existing audio persistence routes

| Route | Location | Current behaviour | Memory-only disposition |
|---|---|---|---|
| Incremental checkpoints | `audio/incremental_saver.rs` | Writes chunk checkpoints under `.checkpoints/` when `auto_save=true` | **Remove / feature-gate** behind `recording-storage` (disabled by default) |
| Final `audio.mp4` merge | `audio/recording_saver.rs` | Merges checkpoints into meeting folder | **Remove / feature-gate** |
| Recording preferences `auto_save` | `audio/recording_preferences.rs` | Default was `true` | **Default forced to `false`**; UI toggle to be removed in Phase 1 |
| Tray quick-save WAV | `tray.rs` | Saves `recording-{ts}.wav` into app data | **Disable / remove** in memory-only build |
| Audio import | `audio/import.rs` | Imports external audio as a meeting | **Hide / gate** (imports conflict with “no recording” product) |
| Retranscription | `audio/retranscription.rs` | Re-runs ASR on stored `audio.mp4` / `.wav` / etc. | **Hide / gate** (no stored audio) |
| FFmpeg encode path | `audio/ffmpeg.rs`, `encode.rs` | Used for saved media containers | **Must not receive live meeting PCM** in memory-only |
| Model downloads | `whisper_engine.rs`, `parakeet_engine.rs`, `summary/.../model_manager.rs` | Writes model weights to disk | **Retained** (not meeting audio) |
| SQLite WAL checkpoint | `database/manager.rs` | `PRAGMA wal_checkpoint` | **Retained** (database integrity, not audio) |
| Transcript / metadata JSON | `recording_saver.rs` meeting folder | Writes transcript + metadata even when `auto_save=false` | **Retained for text**; `audio_file` must stay empty |
| Summary sidecar stdin | `summary/summary_engine/sidecar.rs` | Writes JSON requests to process stdin | **Retained** (text only) |

## Search evidence (initial)

```
rg -n 'File::create|OpenOptions|write_all|tempfile|NamedTempFile|temp_dir|ffmpeg|recording_saver|incremental_saver|checkpoint|audio\.mp4|\.wav|\.mp3|\.m4a|\.flac|\.ogg|\.pcm' \
  meetily/frontend/src-tauri meetily/frontend/src
```

Notable hits:

- `recording_saver.rs` — constructs `IncrementalAudioSaver` only when `auto_save` / `create_checkpoints` is true; otherwise discards PCM chunks.
- `recording_preferences.rs` — previously defaulted `auto_save: true` (changed to `false` in this fork).
- `tray.rs` — allocates `recording-{}.wav` paths.
- `retranscription.rs` — looks for `audio.mp4`, `.m4a`, `.wav`, `.mp3`, `.flac`, `.ogg`.
- Whisper/Parakeet engines — `File::create` for **model** downloads, not live audio.

## Still writable (allowed)

- SQLite database for meetings, committed segments, summaries
- Optional Markdown / JSON text exports
- Atomic text temp files used only for text/JSON/Markdown writes
- Local model caches (Whisper, Parakeet, Ollama) — external to meeting capture

## Not yet proven unreachable

These still exist in the tree and must be compile-time or UI-gated before claiming Phase-1 complete:

1. `IncrementalAudioSaver` construction path if any caller passes `auto_save=true`
2. Tray WAV save actions
3. Import / retranscription UI entry points
4. Any FFmpeg invocation receiving live PCM for container encoding

## Phase-1 exit check

A no-audio-at-rest integration test must assert that a live meeting creates **no** runtime files with audio extensions or audio magic bytes under the app data / temp / meeting folders.
