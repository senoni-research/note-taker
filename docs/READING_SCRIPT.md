# Controlled reading script (smoke test)

Read this aloud at a normal pace. Pause briefly at each blank line.

---

Good morning everyone. Today we are testing local transcription
without saving any audio recordings.

Our first decision is to keep all captured sound in memory only.
Transcript text and meeting notes may be stored on disk.

Action item for Philippe: prepare the pilot test set by Friday.
Action item for Jerome: review the exemplar before we freeze the truth set.

We still have one open question: should system audio and the microphone
run as two separate streams, or as a single mixed stream?

If the weather stays this hot, we should go swimming instead of meetings.
One, two, three, four, five, six, seven, eight, nine, ten.

That concludes the test passage. Thank you.

---

After Ctrl+C, compare `.meetings/<id>/transcript.json` to this script.
