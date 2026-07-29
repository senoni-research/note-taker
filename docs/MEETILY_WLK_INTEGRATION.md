# Meetily → WhisperLiveKit integration

Goal: keep Meetily's cross-platform capture layer, drop its embedded transcription
engines, and stream raw PCM to a loopback WhisperLiveKit server. Captured audio
never reaches disk.

## Toolchain required to build the Rust backend

| Tool | Needed by | Status on this machine |
|---|---|---|
| `cmake` | `whisper-rs-sys` builds `whisper.cpp` | Installed (`brew install cmake`) |
| Node 18+, pnpm | Tauri UI | Installed |
| Full Xcode | Only the optional `core-audio-tap` feature | Not installed, not required |

## The `core-audio-tap` feature

`cidre` provides the macOS Core Audio process tap, and its build script shells
out to `xcodebuild`, so it cannot build against Command Line Tools alone. It is
now an optional dependency behind the `core-audio-tap` feature, which is **off
by default**:

```bash
cargo check                              # no Xcode needed; system audio via CPAL
cargo check --features core-audio-tap    # needs full Xcode
```

Every use of `cidre` sits behind the `core_audio_tap` cfg alias, defined once in
`build.rs`. With the feature off:

- the `capture::core_audio` module and the `StreamBackend::CoreAudio` variant
  are compiled out;
- `AudioCaptureBackend::default()` and `available_backends()` return
  ScreenCaptureKit, and `stream.rs` warns and uses CPAL if a stored preference
  still names Core Audio;
- native device-transport detection and the macOS diagnostics block are skipped,
  falling back to Meetily's name heuristics;
- `trigger_system_audio_permission` becomes a no-op, since CPAL capture is
  covered by the microphone permission.

The `CoreAudio` enum variant itself stays compiled in so stored preferences and
the settings UI keep round-tripping.

The client can also be verified without the app's toolchain through
`tools/asr-protocol-tests`, which compiles the real source file on its own:

```bash
cd tools/asr-protocol-tests
cargo test                      # protocol, sentence splitting, loopback guard
cargo run --example smoke       # live handshake against 127.0.0.1:8000
```

## The client

`meetily/frontend/src-tauri/src/audio/transcription/whisper_livekit.rs` holds a
`WhisperLiveKitSession` plus the diff-protocol state machine. It knows nothing
about Tauri, which is what makes the harness above possible.

- Connect refuses any host that is not loopback, and refuses a server started
  without `--pcm-input`.
- `send_pcm(&[i16])` only enqueues on a channel, so the audio path never blocks
  on the socket.
- Committed lines are split into one segment per sentence, upserted under
  `seg-<line-start-centiseconds>-<index>`; see [ASR_PROTOCOL.md](ASR_PROTOCOL.md).
- Emitted events are `Segment(New | Revised)`, `Provisional`, `ReadyToStop`,
  `Failed`, `Closed`.

## Where to tap the audio

Meetily's pipeline produces two streams (`audio/pipeline.rs`):

1. **VAD speech segments at 16 kHz** → `transcription_sender` (used by the
   whisper/parakeet workers).
2. **Mixed audio at 48 kHz** → the recording saver, only when `auto_save`.

Stream 2 is the correct tap: WhisperLiveKit is a streaming engine that runs its
own VAD, and it closes a transcript line when it hears silence. Feeding it
stream 1 would strip exactly the silences it needs, so it would never break
lines and its timestamps would drift away from wall-clock. Resample the mixed
window 48 kHz → 16 kHz with `audio_processing::resample`, convert to `i16`, and
hand it to the session.

## Build prerequisites for the app itself

`tauri.conf.json` declares two sidecars that must exist before the build script
runs, or it aborts with "resource path ... doesn't exist":

```bash
cd meetily/llama-helper && cargo build --features metal
cp "$(cargo metadata --format-version 1 --no-deps \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')/debug/llama-helper" \
   ../frontend/src-tauri/binaries/llama-helper-aarch64-apple-darwin
```

FFmpeg downloads itself during the build. `llama-helper` is Meetily's bundled
LLM sidecar; this fork generates notes with Ollama instead, so the sidecar is a
candidate for removal later.

One upstream test fails on macOS regardless of these changes:
`test_calculate_buffer_timeout_bluetooth` compares `159.999996ms` with `160ms`.

## Remaining steps

1. Own the session for a recording: start it in
   `audio/recording_commands.rs::start_recording_with_meeting_name`, stop it in
   `stop_recording` (send end-of-audio, wait for `ready_to_stop`).
2. Tap the mixed 48 kHz window in `audio/pipeline.rs` (next to the
   `recording_sender_for_mixed` branch) and stream it 16 kHz mono.
3. Forward `AsrEvent`s as the existing `transcript-update` Tauri event so the
   frontend needs no change: `Segment` → `is_partial: false`, `Provisional` →
   `is_partial: true`, mapping `start_ms`/`end_ms` onto `audio_start_time`/
   `audio_end_time`.
4. Replace the whisper/parakeet engine selection with a WhisperLiveKit
   reachability check in `audio/transcription/engine.rs`, and drop the Parakeet
   model gate in `frontend/src/hooks/useRecordingStart.ts`.
5. Add `ws://127.0.0.1:8000` to `connect-src` in `tauri.conf.json` only if the
   frontend ever talks to the server directly; the Rust client does not need it.
6. Gate the disk-writing recorder behind a non-default `audio-persistence`
   cargo feature (features must be additive, so memory-only is the default
   build) and compile out `incremental_saver`/`encode` with it.
