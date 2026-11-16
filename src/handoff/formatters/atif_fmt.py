"""Render a canonical transcript as ATIF JSON."""

from __future__ import annotations

from handoff.atif import to_atif_json
from handoff.canonical import CanonicalTranscript


def to_atif(transcript: CanonicalTranscript) -> str:
    return to_atif_json(transcript)
