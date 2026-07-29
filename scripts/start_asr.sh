#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

HOST="${WLK_HOST:-127.0.0.1}"
PORT="${WLK_PORT:-8000}"
MODEL="${WLK_MODEL:-small}"
LANG="${WLK_LANGUAGE:-en}"
GLOSSARY="${WLK_GLOSSARY:-}"
# Prefer MLX on Apple Silicon when mlx-whisper is installed
if [[ -z "${WLK_BACKEND:-}" ]]; then
  if python -c "import mlx_whisper" >/dev/null 2>&1; then
    BACKEND="mlx-whisper"
  else
    BACKEND=""
  fi
else
  BACKEND="$WLK_BACKEND"
fi

ARGS=(--host "$HOST" --port "$PORT" --pcm-input --model "$MODEL" --language "$LANG")
if [[ -n "$BACKEND" ]]; then
  ARGS+=(--backend "$BACKEND")
fi
if [[ -n "$GLOSSARY" ]]; then
  ARGS+=(--static-init-prompt "$GLOSSARY")
fi

echo "Starting WhisperLiveKit on ${HOST}:${PORT} (model=${MODEL}, language=${LANG})"
if [[ -n "$GLOSSARY" ]]; then
  echo "Using local terminology glossary"
fi
exec wlk "${ARGS[@]}"
