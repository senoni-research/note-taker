"""Ollama structured meeting notes."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from note_taker.endpoints import assert_loopback

log = logging.getLogger(__name__)

NOTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "executive_summary": {"type": "string"},
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "evidence_segment_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "summary", "evidence_segment_ids"],
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "evidence_segment_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["decision", "evidence_segment_ids"],
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": ["string", "null"]},
                    # The model copies the transcript's own date wording here;
                    # due_date is resolved locally so small models never have to
                    # do calendar arithmetic.
                    "due_date_raw": {"type": ["string", "null"]},
                    # Ollama's grammar parser rejects a pattern combined with
                    # nullable string types. Pydantic enforces ISO dates after
                    # generation and triggers one repair attempt when needed.
                    "due_date": {"type": ["string", "null"]},
                    "evidence_segment_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "task",
                    "owner",
                    "due_date_raw",
                    "due_date",
                    "evidence_segment_ids",
                ],
            },
        },
        "open_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "evidence_segment_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["question", "evidence_segment_ids"],
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "risk": {"type": "string"},
                    "evidence_segment_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["risk", "evidence_segment_ids"],
            },
        },
    },
    "required": [
        "title",
        "executive_summary",
        "topics",
        "decisions",
        "action_items",
        "open_questions",
        "risks",
    ],
}


class StrictNotesModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Topic(StrictNotesModel):
    name: str
    summary: str
    evidence_segment_ids: list[str]


class Decision(StrictNotesModel):
    decision: str
    evidence_segment_ids: list[str]


class ActionItem(StrictNotesModel):
    task: str
    owner: str | None
    due_date_raw: str | None = None
    due_date: str | None
    evidence_segment_ids: list[str]

    @field_validator("due_date")
    @classmethod
    def validate_due_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("due_date must be YYYY-MM-DD or null") from exc


class OpenQuestion(StrictNotesModel):
    question: str
    evidence_segment_ids: list[str]


class Risk(StrictNotesModel):
    risk: str
    evidence_segment_ids: list[str]


class MeetingNotes(StrictNotesModel):
    title: str
    executive_summary: str
    topics: list[Topic] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)


_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_WEEKDAY_RE = re.compile(rf"\b({'|'.join(_WEEKDAYS)})\b")
_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def resolve_due_date(raw: str | None, meeting_date: date) -> str | None:
    """Resolve transcript date wording to an ISO date, or None when ambiguous.

    Handles explicit ISO dates, today/tomorrow, and weekday names (the next
    occurrence strictly after the meeting date). Anything else stays None so the
    notes never claim a date the transcript did not state.
    """
    if not raw:
        return None
    text = raw.strip().lower()

    iso = _ISO_RE.search(text)
    if iso:
        try:
            return date.fromisoformat(iso.group(1)).isoformat()
        except ValueError:
            return None

    if "today" in text:
        return meeting_date.isoformat()
    if "tomorrow" in text:
        return (meeting_date + timedelta(days=1)).isoformat()

    weekday = _WEEKDAY_RE.search(text)
    if weekday:
        delta = (_WEEKDAYS[weekday.group(1)] - meeting_date.weekday()) % 7 or 7
        if "next week" in text:
            delta += 7
        return (meeting_date + timedelta(days=delta)).isoformat()

    return None


def _apply_due_dates(notes: MeetingNotes, meeting_date: date) -> None:
    for item in notes.action_items:
        if item.due_date is None:
            item.due_date = resolve_due_date(item.due_date_raw, meeting_date)


def _relocate_non_iso_due_dates(data: Any) -> Any:
    """Move wording like "Friday" out of due_date so validation can succeed."""
    if not isinstance(data, dict):
        return data
    for item in data.get("action_items") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("due_date")
        if not isinstance(value, str) or _ISO_RE.fullmatch(value.strip()):
            continue
        item["due_date"] = None
        if not item.get("due_date_raw"):
            item["due_date_raw"] = value
    return data


SYSTEM_PROMPT = """Use only information in the supplied transcript.
Never invent attendees, owners, dates, decisions or commitments.
Use null when owner or due date was not explicitly stated.
An unresolved alternative or question is not a decision. Put it only in open_questions.
Only include an open question when the transcript explicitly asks it or says it
is unresolved. Do not turn jokes, suggestions, or declarative statements into questions.
Copy the transcript's own deadline wording verbatim into due_date_raw, for example
"Friday" or "end of the month", and use null when no deadline was stated.
Never compute a calendar date yourself: set due_date to null unless the transcript
states a full date, because the application resolves due_date_raw.
Every decision, action, topic, question and risk must cite one or more valid transcript segment IDs.
Do not include model reasoning or chain-of-thought.
Return only the required JSON object."""


async def generate_notes(
    *,
    ollama_url: str,
    model: str,
    segments: list[dict[str, Any]],
    meeting_date: date | None = None,
    temperature: float = 0.1,
) -> MeetingNotes:
    assert_loopback(ollama_url)
    valid_ids = {str(s["id"]) for s in segments}
    transcript_block = "\n".join(
        f"[{s['id']}] ({s.get('start_ms', 0)}ms) {s['text']}" for s in segments
    )
    effective_date = meeting_date or datetime.now(UTC).date()
    user_content = (
        f"Meeting date: {effective_date.isoformat()}\n"
        f"Transcript segments:\n{transcript_block}\n\n"
        "Produce structured meeting notes as JSON matching the schema."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "think": False,
        "format": NOTES_SCHEMA,
        "options": {"temperature": temperature},
    }

    url = f"{ollama_url.rstrip('/')}/api/chat"
    async with httpx.AsyncClient(timeout=300.0) as client:
        notes = await _chat_validated(client, url, payload, valid_ids)
    _apply_due_dates(notes, effective_date)
    return notes


async def _chat_validated(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    valid_ids: set[str],
) -> MeetingNotes:
    last_error: Exception | None = None
    for attempt in range(2):
        response = await client.post(url, json=payload)
        response.raise_for_status()
        body = response.json()
        content = body.get("message", {}).get("content", "")
        try:
            data = _relocate_non_iso_due_dates(json.loads(content))
            notes = MeetingNotes.model_validate(data)
            _validate_evidence(notes, valid_ids)
            return notes
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            log.warning("Notes validation failed (attempt %s): %s", attempt + 1, exc)
            payload = {
                **payload,
                "messages": payload["messages"]
                + [
                    {
                        "role": "user",
                        "content": (
                            f"Previous output failed validation: {exc}. "
                            "Return corrected JSON only, with valid evidence_segment_ids."
                        ),
                    }
                ],
            }
    raise RuntimeError(f"Ollama notes validation failed: {last_error}")


def _validate_evidence(notes: MeetingNotes, valid_ids: set[str]) -> None:
    buckets = (
        notes.topics,
        notes.decisions,
        notes.action_items,
        notes.open_questions,
        notes.risks,
    )
    for bucket in buckets:
        for item in bucket:
            ids = item.evidence_segment_ids
            for eid in ids:
                if eid not in valid_ids:
                    raise ValueError(f"Unknown evidence segment id: {eid}")
