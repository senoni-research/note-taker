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

## Where the audio is tapped

Meetily's pipeline produces two streams (`audio/pipeline.rs`):

1. **VAD speech segments at 16 kHz** → the whisper/parakeet workers.
2. **Mixed audio at 48 kHz** → the recording saver.

Stream 2 is the tap: WhisperLiveKit is a streaming engine that runs its own VAD,
and it closes a transcript line when it hears silence. Feeding it stream 1 would
strip exactly the silences it needs, so it would never break lines and its
timestamps would drift away from wall-clock.

`RecordingManager::start_recording` therefore hands the pipeline a relay channel
and returns `RecordingStreams { speech_segments, mixed_audio }`. The relay copies
each mixed window to the saver and to streaming ASR; the copy lives in memory
only. `start_transcription_for_provider` gives the chosen provider its stream and
drains the other one, so an unread channel can never grow.

`wlk_stream.rs` resamples 48 kHz → 16 kHz with one persistent `SincFixedIn`,
buffering leftovers across windows rather than starting a resampler per window,
which would add a discontinuity at every boundary. Samples are converted to `i16`
and handed to the session.

## Choosing the provider

Settings → Transcript Model offers **WhisperLiveKit (Streaming)** next to
Parakeet and Local Whisper. It has no model to download, so selecting it saves
the choice immediately (`provider = whisperlivekit`, `model = streaming`); the
other providers still save when a model is picked in their manager. The panel
reports the address and language it will use and can run the real handshake
through `check_whisperlivekit_server`, which also catches a server started
without `--pcm-input`.

`resolve_transcription_provider` picks WhisperLiveKit when `MEETILY_ASR_URL` is
set, or when nothing is stored yet; otherwise the stored choice wins.
`MEETILY_ASR_LANGUAGE` overrides the language, and both default to loopback
and `en`.

```bash
MEETILY_ASR_URL=ws://127.0.0.1:8000 pnpm tauri:dev   # force it for one run
```

Three provider switches needed a streaming arm, and each would have failed
loudly without one:

- `setting.rs` rejects unknown providers when reading the API-key column, so a
  saved `whisperlivekit` config would have broken *every* settings load;
- the model-unload step on stop would have unloaded Whisper, loading an engine
  this fork never uses;
- `useRecordingStart.ts` gated recording on a Parakeet download, which is why it
  now asks `get_active_transcription_provider` first.

Language selection is disabled for this provider, since the server is started
with its own `--language` flag and the UI cannot change it.

## How transcript state reaches the UI

`AsrEvent::Segment` becomes the existing `transcript-update` event, carrying a new
optional `segment_id`. Revisions reuse that id, which is what lets:

- `TranscriptContext` replace the displayed sentence instead of appending a
  second copy of the same speech (it otherwise dedupes on `sequence_id`, which
  advances on every update);
- `recording_saver` upsert by `id` rather than `sequence_id`;
- `indexedDBService` key the recovery record on the segment instead of adding a
  row per revision.

Provisional text is not forwarded. A sentence already appears as soon as its
first words are committed and is then revised in place, so the live feel comes
from revisions rather than from volatile text.

### Known limitation: segment timestamps drift

WhisperLiveKit times whole lines, so per-sentence bounds are interpolated by
character count. The archive re-interpolates a sentence whenever its line grows,
but a timing-only change emits no event, so a persisted `audio_start_time` can
overlap the previous sentence's `audio_end_time`. Text, order and ids are
unaffected, and this fork keeps no audio to sync against. Fixing it means either
emitting timing-only revisions (more events and a `transcripts.json` rewrite per
sentence per update) or flushing a line's final bounds once it stops growing.

## Structured notes instead of a prose summary

`summary/notes.rs` ports `notes.py`: Ollama is constrained to the notes schema and
must cite the transcript segment ids each item came from, and the parts a 4B model
is unreliable at are then corrected locally instead of by prompting again.

- `api_process_transcript` now also takes `segments` (id, `start_ms`, text). The
  flat `text` stays for the providers that summarise prose, and the ids are the
  same ones the transcript view holds, so evidence can be linked back later.
- `process_transcript_background` takes the notes path only when the provider is
  Ollama and segments were sent. Every other provider keeps the upstream markdown
  chunking untouched, which is also the fallback when an older UI sends no
  segments.
- Relative deadlines resolve against the meeting's own `created_at`, not today, so
  regenerating an old meeting cannot move its dates. A deadline the model dropped
  is recovered from the evidence it cited, but only behind a preposition
  (`by Friday`), so "Today we are testing" is not read as a due date.
- Decisions must cite wording that settles something, and open questions must cite
  a question mark or an explicit "still need to decide". Both filters exist
  because a small model otherwise promotes descriptions to decisions and jokes to
  open questions; dropped items remain covered by the summary and topics.
- The endpoint is asserted to be loopback before the transcript is sent.
- Long meetings are windowed by the same token threshold as upstream and merged,
  dropping repeats; each window cites its own segments, so evidence stays valid.

The rendered markdown flows into the existing editor, and its `# Title` still
names the meeting. The notes JSON is stored beside the markdown under `notes`, so
a later evidence view needs no regeneration.

Language selection does not apply: notes are written in English.

Run the live check against a real server with
`cargo test --lib summary::notes::tests::live -- --ignored --nocapture`
(`NOTES_TEST_MODEL` picks the model).

## The bundled LLM sidecar is gone

Upstream ships `llama-helper`, a llama.cpp sidecar that ran GGUF models the app
downloaded itself, behind a `builtin-ai` provider. Ollama already serves models
locally, so keeping a second inference stack meant a second model store, a
second download UI, and a sidecar process to supervise. It is removed:

- the `llama-helper` crate, its `externalBin` entry, its workspace member and the
  sidecar build steps in `build-gpu`/`dev-gpu` (both shells) are deleted, so the
  app now builds without pre-building a sidecar binary;
- `summary_engine/` (sidecar supervisor, model registry, downloader, its Tauri
  commands and startup/shutdown hooks) is deleted, along with the `app_data_dir`
  argument it needed threaded through the whole summary path;
- the frontend loses the provider option, its model manager, the onboarding
  download card and the download-progress listeners.

Two compatibility details keep existing installs working. `LLMProvider::from_str`
still accepts `builtin-ai` and reads it as Ollama, because the API-key lookups run
against whatever string the settings row holds; and a migration rewrites a stored
`builtin-ai` row to Ollama with this fork's default model, since the GGUF name it
saved means nothing to Ollama and generation would otherwise fail.

Fresh installs now default to the streaming transcription provider and an Ollama
summary model, so nothing is downloaded through the app at all. Onboarding still
downloads Parakeet, which audio import and re-transcription need.

FFmpeg is the only remaining sidecar, and it downloads itself during the build.

One upstream test fails on macOS regardless of these changes:
`test_calculate_buffer_timeout_bluetooth` compares `159.999996ms` with `160ms`.

## Verified end to end

Recording from the UI on 2026-07-29 produced sentence segments through the full
path, and the meeting folder held only `metadata.json` and `transcripts.json`:

```
seg-00000112-00  1.12→2.27   "Hey, good morning."
seg-00000112-01  3.63→6.14   "How are you doing?"
seg-00000112-02  4.39→9.94   "Uh, anyway, I wanted to tell you something about the weather."
```

The stop sequence completed cleanly: the pipeline closed the relay, the task sent
end-of-audio, and the session reported `ready_to_stop` before the recording was
saved.

## Remaining steps

1. Stop running Meetily's VAD when a streaming provider is active; its speech
   segments are currently computed and then drained.
2. Decide what audio import and re-transcription should do while this provider
   is selected: both need a batch engine, so they still fall back to a Whisper
   or Parakeet model from their own dialog.
3. Gate the disk-writing recorder behind a non-default `audio-persistence`
   cargo feature (features must be additive, so memory-only is the default
   build) and compile out `incremental_saver`/`encode` with it.
5. Decide on the segment-timestamp drift described above.
