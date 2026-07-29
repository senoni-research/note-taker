from itertools import pairwise

from note_taker.asr import parse_asr_message, parse_wlk_time
from note_taker.meeting import TranscriptArchive


def test_parse_wlk_time_string():
    assert parse_wlk_time("0:00:07.17") == 7.17
    assert parse_wlk_time("0:01:02.05") == 62.05
    assert abs(parse_wlk_time(3.5) - 3.5) < 1e-9


def test_parse_wlk_time_does_not_repeat_strings():
    seconds = parse_wlk_time("0:00:07.17")
    assert seconds is not None
    assert int(seconds * 1000) == 7170


def test_silence_line_counts_toward_n_lines():
    archive = TranscriptArchive("m1")
    diff = parse_asr_message(
        {
            "type": "diff",
            "seq": 2,
            "n_lines": 1,
            "new_lines": [
                {"speaker": -2, "text": "", "start": "0:00:01.00", "end": "0:00:02.00"}
            ],
            "buffer_transcription": "",
        }
    )
    events = archive.apply(diff)
    assert events == []
    assert len(archive.server_window) == 1
    assert archive.out_of_sync is False


def _revise(archive: TranscriptArchive, text: str, end: str) -> list[tuple[str, dict]]:
    return archive.apply(
        parse_asr_message(
            {
                "type": "diff",
                "seq": 1,
                "n_lines": 1,
                "new_lines": [
                    {
                        "speaker": 1,
                        "text": text,
                        "start": "0:00:01.64",
                        "end": end,
                    }
                ],
                "buffer_transcription": "",
            }
        )
    )


def test_growing_line_splits_into_sentence_segments():
    archive = TranscriptArchive("m1")
    events = []
    for text, end in [
        ("Hello.", "0:00:02.00"),
        ("Hello. What are you doing?", "0:00:05.00"),
        (
            "Hello. What are you doing? Good. Um, so today the purpose is to understand what we are going to do",
            "0:00:15.00",
        ),
    ]:
        events.extend(_revise(archive, text, end))

    texts = [seg["text"] for seg in archive.segments]
    assert texts == [
        "Hello.",
        "What are you doing?",
        "Good.",
        "Um, so today the purpose is to understand what we are going to do",
    ]
    assert [seg["id"] for seg in archive.segments] == [
        "seg-00000164-00",
        "seg-00000164-01",
        "seg-00000164-02",
        "seg-00000164-03",
    ]
    # Settled sentences must not re-emit when later ones arrive.
    assert [event for event, _ in events] == ["new"] * 4
    starts = [seg["start_ms"] for seg in archive.segments]
    assert starts == sorted(starts)
    assert archive.segments[0]["start_ms"] == 1640


def test_sentence_timings_do_not_overlap_as_line_grows():
    archive = TranscriptArchive("m1")
    _revise(archive, "One two three.", "0:00:04.00")
    _revise(archive, "One two three. Four five six.", "0:00:08.00")
    _revise(archive, "One two three. Four five six. Seven eight nine.", "0:00:12.00")

    bounds = [(seg["start_ms"], seg["end_ms"]) for seg in archive.segments]
    assert bounds[0][0] == 1640
    for (_, prev_end), (next_start, _) in pairwise(bounds):
        assert prev_end <= next_start


def test_merged_revision_drops_stale_sentence_segments():
    archive = TranscriptArchive("m1")
    _revise(archive, "Ready. Set. Go.", "0:00:06.00")
    assert len(archive.segments) == 3

    _revise(archive, "Ready, set, go.", "0:00:06.00")
    assert [seg["id"] for seg in archive.segments] == ["seg-00000164-00"]
    assert archive.segments[0]["text"] == "Ready, set, go."


def test_end_ms_only_change_is_silent():
    archive = TranscriptArchive("m1")
    first = archive.apply(
        parse_asr_message(
            {
                "type": "diff",
                "seq": 1,
                "n_lines": 1,
                "new_lines": [
                    {
                        "speaker": 1,
                        "text": "Hello there.",
                        "start": "0:00:01.00",
                        "end": "0:00:02.00",
                    }
                ],
                "buffer_transcription": "",
            }
        )
    )
    second = archive.apply(
        parse_asr_message(
            {
                "type": "diff",
                "seq": 2,
                "n_lines": 1,
                "new_lines": [
                    {
                        "speaker": 1,
                        "text": "Hello there.",
                        "start": "0:00:01.00",
                        "end": "0:00:02.50",
                    }
                ],
                "buffer_transcription": "",
            }
        )
    )
    assert len(first) == 1 and first[0][0] == "new"
    assert second == []
    assert archive.segments[0]["end_ms"] == 2500
    assert len(archive.segments) == 1


def test_new_utterance_gets_new_segment():
    archive = TranscriptArchive("m1")
    archive.apply(
        parse_asr_message(
            {
                "type": "snapshot",
                "seq": 1,
                "lines": [
                    {
                        "speaker": 1,
                        "text": "Hello there.",
                        "start": "0:00:01.00",
                        "end": "0:00:02.00",
                    }
                ],
                "buffer_transcription": "",
            }
        )
    )
    events = archive.apply(
        parse_asr_message(
            {
                "type": "diff",
                "seq": 2,
                "n_lines": 2,
                "new_lines": [
                    {
                        "speaker": 1,
                        "text": "Okay, very good.",
                        "start": "0:00:34.56",
                        "end": "0:00:35.44",
                    }
                ],
                "buffer_transcription": "",
            }
        )
    )
    assert len(archive.segments) == 2
    assert events[0][0] == "new"
    assert archive.segments[1]["id"] == "seg-00003456-00"

