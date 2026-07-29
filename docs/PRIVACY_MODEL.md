# Privacy model

## User-facing statement

> Captured meeting audio is processed in memory and is never intentionally written to an audio file by this application. Transcript text and generated notes are stored locally.

## What we guarantee

The application will not intentionally create:

- a normal audio file (wav/mp3/m4a/mp4/flac/ogg/opus/webm/pcm/…)
- a temporary audio file
- an audio checkpoint
- an audio SQLite/blob column
- an encoded media container for a live meeting
- audio bytes in application logs

Captured PCM lives only in:

- OS / device audio buffers
- bounded application RAM rings
- the local WhisperLiveKit process RAM

On stop or abort, application-held PCM buffers are wiped. Committed transcript text may already have been persisted; provisional ASR text is not.

## What we do **not** claim

A normal desktop app cannot truthfully promise that the OS never pages process memory to swap, or that crash dumps never contain residual PCM. Optional `mlock` / `VirtualLock` may reduce swap risk when granted; if locking fails, the UI must not claim swap protection.

## Loopback only

By default ASR and LLM traffic is limited to `127.0.0.1`, `::1`, and `localhost`. No cloud fallback for transcripts or notes.
