from __future__ import annotations

import json

from handoff.canonical import CanonicalTranscript


def to_json(t: CanonicalTranscript, *, indent: int | None = 2) -> str:
    return json.dumps(t.to_dict(), indent=indent, ensure_ascii=False)
