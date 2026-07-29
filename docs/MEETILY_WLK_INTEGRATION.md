# Meetily → WhisperLiveKit integration

Goal: keep Meetily's cross-platform capture layer, drop its embedded transcription
engines, and stream raw PCM to a loopback WhisperLiveKit server. Captured audio
never reaches disk.

## Toolchain required to build the Rust backend

| Tool | Needed by | Status on this machine |
|---|---|---|
| `cmake` | `whisper-rs-sys` builds `whisper.cpp` | Installed (`brew install cmake`) |
| **Full Xcode** | `cidre` runs `xcodebuild` for its Core Audio bindings | **Missing — blocks `cargo check`** |
| Node 18+, pnpm | Tauri UI | Installed |

Command Line Tools alone are not enough: `cidre`'s build script calls
`xcodebuild` and fails with "requires Xcode". Either install Xcode and run
`sudo xcode-select -s /Applications/Xcode.app`, or feature-gate `cidre` (macOS
system-audio tap) so a build can fall back to the CPAL output-device path.

Until then the client is verified through `tools/asr-protocol-tests`, which
compiles the real source file on its own:

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
