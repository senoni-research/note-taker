//! Compiles the Meetily WhisperLiveKit client in isolation so `cargo test` can
//! exercise its protocol handling on machines without the full Tauri toolchain.

#[path = "../../../meetily/frontend/src-tauri/src/audio/transcription/whisper_livekit.rs"]
pub mod whisper_livekit;
