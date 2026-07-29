# Local setup — Windows

## 1. Ollama

Install [OllamaSetup.exe](https://ollama.com/download). Requires Windows 10 22H2+.

```powershell
ollama --version
ollama pull qwen3.5:4b
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:11434/api/tags"
```

Models live under `%HOMEPATH%\.ollama` (override with `OLLAMA_MODELS`).

## 2. WhisperLiveKit with uv

```powershell
cd note_taker
uv venv .venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -e .
wlk check
wlk --host 127.0.0.1 --port 8000 --pcm-input --model small --language en
```

Optional NVIDIA:

```powershell
uv pip install -e ".[cu129]"   # if using WhisperLiveKit source profile
```

Prove CPU first before adding CUDA.

## 3. Meetily desktop app

Prerequisites:

- Node.js 18+
- pnpm 8+
- Rust stable-MSVC
- Visual Studio Build Tools with **Desktop development with C++**

```powershell
npm install -g pnpm
rustup default stable-msvc
cd meetily\frontend
pnpm install
pnpm run tauri:dev
```

Grant microphone access under Settings → Privacy & security → Microphone. System audio uses WASAPI loopback (no virtual cable required).

## 4. LM Studio alternative

Provider: OpenAI-compatible  
Base URL: `http://127.0.0.1:1234/v1`  
Keep “Serve on Local Network” **disabled**.
