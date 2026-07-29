# Local setup — macOS

## Prerequisites checked on this machine

| Tool | Status |
|---|---|
| `uv` | Present |
| Python 3.12 (Homebrew) | Present |
| Ollama | Present (`0.32.1`), API on `127.0.0.1:11434` |
| Rust / Cargo | Present |
| ffmpeg (for WLK check) | Present |
| Node.js / pnpm | **Missing — install required for Meetily UI** |

## 1. Project Python env (already created)

```bash
cd note-taker
source .venv/bin/activate
wlk --version
wlk check
```

Optional Apple Silicon acceleration:

```bash
uv pip install mlx-whisper
# then:
wlk --backend mlx-whisper --host 127.0.0.1 --port 8000 --pcm-input --model small --language en
```

## 2. Ollama

Already running. Verify:

```bash
ollama --version
curl http://127.0.0.1:11434/api/tags
```

Recommended note model (small / fast):

```bash
ollama pull qwen3.5:4b
```

You already have large models (`gpt-oss:120b`, `cogito:70b`, …). Those work but are heavy for structured meeting notes.

## 3. WhisperLiveKit server

```bash
source .venv/bin/activate
./scripts/start_asr.sh
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

First run downloads the Whisper model weights.

## 4. Meetily desktop app

Install toolchain (you need to run this):

```bash
xcode-select --install   # if needed
brew install node
npm install -g pnpm
```

Then:

```bash
cd meetily/frontend
pnpm install
pnpm run tauri:dev
```

Grant **Microphone** and **Screen & System Audio Recording** under System Settings → Privacy & Security.

## 5. Python reference CLI

```bash
source .venv/bin/activate
uv pip install -e .
note-taker meet --language en --ollama-model qwen3.5:4b
```

Audio never leaves RAM in this CLI; only transcript JSON / notes are written under `./.meetings/` when you stop.
