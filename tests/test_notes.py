from datetime import date

import pytest
from pydantic import ValidationError

from note_taker.notes import (
    ActionItem,
    Decision,
    MeetingNotes,
    OpenQuestion,
    _apply_due_dates,
    _prune_unsupported,
    _relocate_non_iso_due_dates,
    _validate_evidence,
    resolve_due_date,
)

WEDNESDAY = date(2026, 7, 29)


def test_action_due_date_requires_iso_or_null():
    valid = ActionItem(
        task="Prepare the pilot.",
        owner="Philippe",
        due_date="2026-07-31",
        evidence_segment_ids=["seg-1"],
    )
    assert valid.due_date == "2026-07-31"

    with pytest.raises(ValidationError):
        ActionItem(
            task="Prepare the pilot.",
            owner="Philippe",
            due_date="Friday",
            evidence_segment_ids=["seg-1"],
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Friday", "2026-07-31"),
        ("by friday.", "2026-07-31"),
        ("Wednesday", "2026-08-05"),
        ("Monday next week", "2026-08-10"),
        ("today", "2026-07-29"),
        ("tomorrow", "2026-07-30"),
        ("2026-09-01", "2026-09-01"),
        ("end of the month", None),
        ("soon", None),
        (None, None),
    ],
)
def test_resolve_due_date(raw, expected):
    assert resolve_due_date(raw, WEDNESDAY) == expected


def test_iso_due_date_is_left_in_place():
    data = _relocate_non_iso_due_dates(
        {"action_items": [{"task": "Ship.", "due_date": "2026-09-01"}]}
    )
    assert data["action_items"][0]["due_date"] == "2026-09-01"
    assert data["action_items"][0].get("due_date_raw") is None


def test_non_iso_due_date_is_relocated_then_resolved():
    data = _relocate_non_iso_due_dates(
        {
            "action_items": [
                {
                    "task": "Prepare the pilot.",
                    "owner": "Philippe",
                    "due_date": "Friday",
                    "evidence_segment_ids": ["seg-1"],
                }
            ]
        }
    )
    notes = MeetingNotes(
        title="T",
        executive_summary="S",
        action_items=[ActionItem(**data["action_items"][0])],
    )
    _apply_due_dates(notes, WEDNESDAY, {})

    item = notes.action_items[0]
    assert item.due_date_raw == "Friday"
    assert item.due_date == "2026-07-31"


def test_due_date_recovered_from_cited_evidence():
    notes = MeetingNotes(
        title="T",
        executive_summary="S",
        action_items=[
            ActionItem(
                task="Prepare the pilot test set",
                owner="Philippe",
                due_date_raw=None,
                due_date=None,
                evidence_segment_ids=["seg-a", "seg-b"],
            )
        ],
    )
    texts = {
        "seg-a": "Action item for Philippe.",
        "seg-b": "Prepare the pilot test set by Friday.",
    }

    _apply_due_dates(notes, WEDNESDAY, texts)

    assert notes.action_items[0].due_date_raw == "Friday"
    assert notes.action_items[0].due_date == "2026-07-31"


def test_incidental_date_word_is_not_a_deadline():
    notes = MeetingNotes(
        title="T",
        executive_summary="S",
        action_items=[
            ActionItem(
                task="Test transcription",
                owner=None,
                due_date_raw=None,
                due_date=None,
                evidence_segment_ids=["seg-a"],
            )
        ],
    )
    texts = {"seg-a": "Today we are testing local transcription on Monday's build."}

    _apply_due_dates(notes, WEDNESDAY, texts)

    assert notes.action_items[0].due_date is None


def test_unresolvable_due_date_stays_null():
    notes = MeetingNotes(
        title="T",
        executive_summary="S",
        action_items=[
            ActionItem(
                task="Ship it.",
                owner=None,
                due_date_raw="as soon as possible",
                due_date=None,
                evidence_segment_ids=["seg-1"],
            )
        ],
    )
    _apply_due_dates(notes, WEDNESDAY, {})
    assert notes.action_items[0].due_date is None


def test_prune_keeps_only_evidence_backed_decisions_and_questions():
    texts = {
        "seg-a": "Our first decision is to keep all captured sound in memory only.",
        "seg-b": "Transcript text and meeting notes may be on disk.",
        "seg-c": "Should audio and the microphone run as two streams or one?",
        "seg-d": "If the weather stays this hot, we should go swimming.",
        "seg-e": "We still need to decide how long the ring buffer should be.",
    }
    notes = MeetingNotes(
        title="T",
        executive_summary="S",
        decisions=[
            Decision(decision="Keep audio in memory only.", evidence_segment_ids=["seg-a"]),
            Decision(decision="Notes may be on disk.", evidence_segment_ids=["seg-b"]),
        ],
        open_questions=[
            OpenQuestion(question="One stream or two?", evidence_segment_ids=["seg-c"]),
            OpenQuestion(question="Go swimming?", evidence_segment_ids=["seg-d"]),
            OpenQuestion(question="Ring buffer length?", evidence_segment_ids=["seg-e"]),
        ],
    )

    _prune_unsupported(notes, texts)

    assert [d.decision for d in notes.decisions] == ["Keep audio in memory only."]
    assert [q.question for q in notes.open_questions] == [
        "One stream or two?",
        "Ring buffer length?",
    ]


def test_nested_notes_reject_unknown_evidence():
    notes = MeetingNotes(
        title="Test",
        executive_summary="Summary",
        topics=[],
        decisions=[
            Decision(decision="Use memory-only audio.", evidence_segment_ids=["seg-missing"])
        ],
        action_items=[],
        open_questions=[
            OpenQuestion(question="Mixed or dual?", evidence_segment_ids=["seg-1"])
        ],
        risks=[],
    )

    with pytest.raises(ValueError, match="Unknown evidence"):
        _validate_evidence(notes, {"seg-1"})
