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

Two behaviours are deliberately handled on the client rather than by a model:

- **Sentence segments** — WhisperLiveKit only breaks a line on VAD silence, so committed lines are split into one segment per sentence (see [docs/ASR_PROTOCOL.md](docs/ASR_PROTOCOL.md)).
- **Deadlines** — the model copies the transcript's wording into `due_date_raw` (`"Friday"`), and `due_date` is resolved locally against the meeting date. Wording the resolver does not understand stays `null` instead of becoming an invented date.

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

## Status

| Component | Status |
|---|---|
| WhisperLiveKit venv | Installed |
| Ollama API (existing) | Detected at `127.0.0.1:11434` |
| Python memory-only CLI | In progress |
| Meetily memory-only fork | Started (`auto_save` default → false) |
| Node / pnpm / Tauri UI | **Blocked — install Node + pnpm** |
