# Local setup — Ubuntu on WSL2

WSL2 has no audio hardware. WSLg bridges Windows audio into the VM over RDP as two
PulseAudio endpoints: `RDPSource` (the Windows **microphone**) and `RDPSink` (the Windows
speakers). There is no monitor of Windows playback, so out of the box this environment hears
you but not the other people in a call; §5 covers how to route their audio in.

| Piece | Under WSL2 |
|---|---|
| Python CLI (`note-taker meet --transcript-only`) | Works, after routing ALSA to WSLg's PulseAudio (§3) |
| WhisperLiveKit server | Works |
| Microphone | Works via `RDPSource`, if Windows mic permission is granted |
| Windows playback (far end of a call) | Only through a virtual input device — see §5 |
| Ollama | Works, but `--transcript-only` does not need it at all |
| Meetily desktop app | Not worth it here — run it natively on Windows, see [LOCAL_SETUP_WINDOWS.md](LOCAL_SETUP_WINDOWS.md) |

For a transcript with no summary this is a complete setup: WhisperLiveKit plus the CLI, no
LLM anywhere in the loop.

## 1. System packages

```bash
sudo apt update
sudo apt install -y build-essential \
  libportaudio2 libasound2-plugins alsa-utils pulseaudio-utils
```

`libportaudio2` is what `sounddevice` binds to, and `libasound2-plugins` provides the ALSA
plugin that forwards to PulseAudio. `uv` downloads its own Python 3.12, so no interpreter
package is needed (`apt install python3.12` does not exist before Ubuntu 24.04 anyway).

## 2. Python env

```bash
cd note-taker
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e .
```

Skip `mlx-whisper`; it is Apple-only.

## 3. Route audio to WSLg

PortAudio opens ALSA, and WSL2 has no ALSA cards, so without this step the CLI fails with
`Invalid input device`. Point ALSA's default device at PulseAudio:

```bash
cat > ~/.asoundrc <<'EOF'
pcm.!default { type pulse fallback "sysdefault" }
ctl.!default { type pulse fallback "sysdefault" }
EOF
```

WSLg sets `PULSE_SERVER=unix:/mnt/wslg/PulseServer` itself. If it is missing, WSLg is not
running and audio cannot work at all:

```bash
echo "$PULSE_SERVER"
pactl list sources short   # expect RDPSource
```

Grant the mic first under Windows Settings → Privacy & security → Microphone (both
"Microphone access" and "Let desktop apps access your microphone"). Then confirm real
samples arrive, rather than trusting the device list:

```bash
arecord -f S16_LE -r 16000 -c 1 -d 3 -V mono /dev/null
python -c "import sounddevice; print(sounddevice.query_devices(kind='input'))"
```

Changing the Windows sound device after WSL starts tends to break the bridge; `wsl
--shutdown` in PowerShell and reopening the shell restores it.

## 4. Run it

Two terminals, both inside WSL:

```bash
source .venv/bin/activate
WLK_MODEL=small ./scripts/start_asr.sh
```

```bash
source .venv/bin/activate
note-taker health --transcript-only          # ASR endpoint only, no Ollama check
note-taker meet --language en --transcript-only
```

Ctrl+C stops it. Under `.meetings/<id>/` you get `transcript.txt` (`[mm:ss] text` per
sentence) and `transcript.json` (the same segments with ids and millisecond timings). Both
are written continuously, so killing the process still leaves the transcript on disk. No
audio file is ever created, and `--transcript-only` never contacts Ollama.

On model size: `start_asr.sh` only selects the MLX backend when `mlx_whisper` imports, so on
Linux it falls through to WhisperLiveKit's default. Check `nvidia-smi` — it works inside
WSL2 with a current Windows driver, and WhisperLiveKit will use the GPU, in which case
`medium` or `large-v3` is fine. On CPU alone stay at `small`.

## 5. Getting the far end of a call

WSLg forwards whatever Windows has selected as its **default recording device**, so the way
to capture other participants is to make that device a loopback of Windows playback:

- Some sound drivers expose **Stereo Mix** (Sound Control Panel → Recording → right-click →
  Show Disabled Devices). Enable it and set it as default.
- Otherwise a virtual cable (VB-Audio Cable, VoiceMeeter) does the same. VoiceMeeter is the
  option that can mix your mic *and* system playback into one device, which is what you
  want for a two-sided transcript.

Nothing in WSL can do this for you. The Linux code path in Meetily looks for a PulseAudio
monitor source:

```
src-tauri/src/audio/devices/platform/linux.rs — devices whose name contains "monitor"
```

On a real Ubuntu desktop that monitor mirrors whatever the machine plays, but under WSLg the
only sink is `RDPSink`, whose monitor carries audio produced *inside* WSL. Teams and Zoom
running on Windows never touch it.

## 6. Optional: notes as well

Only if you later want summaries. Install Ollama **inside** WSL so everything stays on the
VM's loopback, then drop the flag:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.5:4b
note-taker meet --language en --ollama-model qwen3.5:4b
```

Reusing an Ollama you already run on Windows is possible but costs privacy: it needs
`OLLAMA_HOST=0.0.0.0`, a firewall exception, and either mirrored networking
(`networkingMode=mirrored` in `.wslconfig`, which makes `127.0.0.1` reach Windows) or the
host IP from `ip route`. A second copy of a 4B model is the cheaper trade.
