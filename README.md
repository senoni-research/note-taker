# note_taker

Local meeting notetaker: **audio stays in RAM**, only transcript text and generated notes are stored.

## Architecture

| Layer | Choice |
|---|---|
| Desktop capture (target) | Fork of [Meetily](https://github.com/Zackriya-Solutions/meetily) (`meetily/`) |
| Streaming ASR | [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) on loopback |
| Notes / actions | Local [Ollama](https://ollama.com) (or OpenAI-compatible local endpoint) |
| Privacy model | Bounded PCM ring buffers; no audio files, checkpoints, or audio blobs |

Meetily already solves mic + system-audio capture on macOS/Windows. This project converts it to a **memory-only** build and streams raw PCM to WhisperLiveKit instead of saving recordings.

A Python reference CLI under `src/note_taker/` proves the ASR + Ollama loop without the Tauri UI.

Some behaviours are deliberately handled on the client rather than by a model, because a 4B model is not consistent enough at them:

- **Sentence segments** — WhisperLiveKit only breaks a line on VAD silence, so committed lines are split into one segment per sentence (see [docs/ASR_PROTOCOL.md](docs/ASR_PROTOCOL.md)).
- **Deadlines** — the model copies the transcript's wording into `due_date_raw` (`"by Friday"`), and `due_date` is resolved locally against the meeting date. If the model drops the deadline, it is recovered from the evidence it cited, but only from a deadline phrase (`by`/`before`/`due`/`until`). Wording the resolver does not understand stays `null` rather than becoming an invented date.
- **Evidence-backed decisions and questions** — a decision is kept only if its cited segments actually contain decision wording, and an open question only if they contain a question mark or an unresolved marker. Dropped items are logged and still covered by the summary and topics.

Terminology that ASR tends to mangle (names, `truth set`, `single mixed stream`) lives in [config/glossary.txt](config/glossary.txt) and is passed to Whisper as a static init prompt.

## Quick start (macOS)

### Already done in this folder

```bash
source .venv/bin/activate   # uv-managed Python 3.12
wlk --version               # WhisperLiveKit 0.2.24
```

### You need to install (please run these)

1. **Node.js 18+ and pnpm** — required to build/run Meetily’s Tauri UI:

```bash
brew install node
npm install -g pnpm
```

2. **(Recommended on Apple Silicon) MLX Whisper backend:**

```bash
source .venv/bin/activate
uv pip install mlx-whisper
```

3. **(Optional) Smaller summarisation model** — you already have large Ollama models; for notes, a 4B model is usually enough:

```bash
ollama pull qwen3.5:4b
```

### Run WhisperLiveKit

```bash
source .venv/bin/activate
./scripts/start_asr.sh
# or:
wlk --host 127.0.0.1 --port 8000 --pcm-input --model small --language en
```

### Run the Python mic prototype

```bash
source .venv/bin/activate
uv pip install -e .
note-taker meet --language en
# Ctrl+C to stop → generates notes via Ollama
```

### Run Meetily (after Node/pnpm)

```bash
cd meetily/frontend
pnpm install
pnpm run tauri:dev
```

## Docs

- [docs/AUDIO_STORAGE_AUDIT.md](docs/AUDIO_STORAGE_AUDIT.md) — Meetily audio-at-rest paths
- [docs/PRIVACY_MODEL.md](docs/PRIVACY_MODEL.md) — exact privacy guarantee
- [docs/LOCAL_SETUP_MACOS.md](docs/LOCAL_SETUP_MACOS.md)
- [docs/LOCAL_SETUP_WINDOWS.md](docs/LOCAL_SETUP_WINDOWS.md)
- [docs/ASR_PROTOCOL.md](docs/ASR_PROTOCOL.md)
- [docs/MEETILY_WLK_INTEGRATION.md](docs/MEETILY_WLK_INTEGRATION.md) — desktop-app wiring plan and toolchain

## Status

| Component | Status |
|---|---|
| WhisperLiveKit venv | Installed |
| Ollama API (existing) | Detected at `127.0.0.1:11434` |
| Python memory-only CLI | Working end to end |
| Meetily WhisperLiveKit client | Written and unit-tested; not yet wired into the pipeline |
| Meetily memory-only fork | `auto_save` default → false, on branch `memory-only` |
| Meetily Rust build | **Blocked — `cidre` needs full Xcode** (see integration doc) |
