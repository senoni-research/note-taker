#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

HOST="${WLK_HOST:-127.0.0.1}"
PORT="${WLK_PORT:-8000}"
MODEL="${WLK_MODEL:-medium}"
LANG="${WLK_LANGUAGE:-en}"

# The glossary biases decoding towards our names and terminology. WhisperLiveKit
# only forwards it under the simulstreaming policy, which is its default.
GLOSSARY_FILE="${WLK_GLOSSARY_FILE:-$ROOT/config/glossary.txt}"
if [[ -n "${WLK_GLOSSARY:-}" ]]; then
  GLOSSARY="$WLK_GLOSSARY"
  GLOSSARY_SOURCE="WLK_GLOSSARY"
elif [[ -f "$GLOSSARY_FILE" ]]; then
  GLOSSARY="$(tr '\n' ' ' < "$GLOSSARY_FILE")"
  GLOSSARY_SOURCE="$GLOSSARY_FILE"
else
  GLOSSARY=""
  GLOSSARY_SOURCE=""
fi
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
  echo "Glossary: $GLOSSARY_SOURCE"
fi
exec wlk "${ARGS[@]}"
