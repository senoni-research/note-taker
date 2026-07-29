//! Connectivity smoke test against a running WhisperLiveKit server.
//!
//! Streams two seconds of silence as 16 kHz mono PCM and prints whatever the
//! server reports, which is enough to prove the handshake, the binary framing
//! and the stop sequence work end to end.
//!
//! Run with: cargo run --example smoke

use asr_protocol_tests::whisper_livekit::{AsrEvent, WhisperLiveKitSession};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let (session, mut events) = WhisperLiveKitSession::connect("ws://127.0.0.1:8000", "en").await?;
    println!("connected");

    // 250 ms frames of silence, as the pipeline would deliver them.
    let frame = vec![0i16; 4000];
    for _ in 0..8 {
        if !session.send_pcm(&frame) {
            anyhow::bail!("session closed while streaming");
        }
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
    }

    session.end_of_audio();
    println!("sent end-of-audio");

    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(20);
    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            println!("timed out waiting for ready_to_stop");
            break;
        }
        match tokio::time::timeout(remaining, events.recv()).await {
            Ok(Some(AsrEvent::Segment(event))) => println!("segment: {event:?}"),
            Ok(Some(AsrEvent::Provisional(text))) => println!("provisional: {text:?}"),
            Ok(Some(AsrEvent::ReadyToStop)) => {
                println!("ready_to_stop");
                break;
            }
            Ok(Some(AsrEvent::Failed(message))) => {
                println!("failed: {message}");
                break;
            }
            Ok(Some(AsrEvent::Closed)) | Ok(None) => {
                println!("closed");
                break;
            }
            Err(_) => {
                println!("timed out waiting for ready_to_stop");
                break;
            }
        }
    }

    Ok(())
}
