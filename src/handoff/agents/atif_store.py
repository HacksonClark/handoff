"""ATIF file-store adapter — portable trajectory interchange.

Stores ATIF ``trajectory.json`` documents under::

    ~/.handoff/trajectories/<encoded-cwd>/<session_id>.json

Use this agent as a neutral interchange hop::

    handoff cursor atif          # normalize Cursor → ATIF on disk
    handoff atif claude          # inject ATIF into Claude Code
    handoff codex claude --format atif --no-inject > traj.json

Or as a Harbor/Terminal-Bench bridge: drop a trial's ``agent/trajectory.json``
into the store (or point ``atif_home`` at a directory of trajectories).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from handoff.agents.base import (
    Extractor,
    Injector,
    SessionRef,
    register_extractor,
    register_injector,
)
from handoff.atif import from_atif, to_atif, validate_atif
from handoff.canonical import CanonicalTranscript, now_iso


def encode_cwd(cwd: Path | str) -> str:
    p = str(Path(cwd).expanduser().resolve())
    if "\x00" in p:
        raise ValueError("null byte in cwd")
    encoded = p.replace("/", "-").replace("\\", "-")
    if not encoded or "/" in encoded or "\\" in encoded:
        raise ValueError(f"invalid cwd encoding: {encoded!r}")
    return encoded


def _mtime_iso(path: Path) -> str:
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except OSError:
        return now_iso()


class AtifExtractor(Extractor):
    agent_name = "atif"

    def _root(self) -> Path:
        return self.home

    def list_sessions(self, cwd: Path | None = None) -> list[SessionRef]:
        root = self._root()
        files: list[Path] = []
        if cwd is not None:
            if root.is_dir():
                project = root / encode_cwd(cwd)
                if project.is_dir():
                    files.extend(project.glob("*.json"))
            # Harbor / Terminal-Bench trial drops — discover even when the
            # configured atif_home directory does not exist yet.
            direct = Path(cwd) / "trajectory.json"
            if direct.is_file():
                files.append(direct)
            agent_traj = Path(cwd) / "agent" / "trajectory.json"
            if agent_traj.is_file():
                files.append(agent_traj)
        elif root.is_dir():
            files.extend(root.rglob("*.json"))
        else:
            return []

        refs: list[SessionRef] = []
        for path in files:
            ref = self._ref_from_path(path, cwd)
            if ref:
                refs.append(ref)
        refs.sort(key=lambda r: r.last_activity or "", reverse=True)
        return refs

    def _ref_from_path(self, path: Path, cwd: Path | None) -> SessionRef | None:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("schema_version") and not str(data["schema_version"]).startswith("ATIF-"):
            return None
        if "steps" not in data and "agent" not in data:
            return None

        session_id = str(data.get("session_id") or data.get("trajectory_id") or path.stem)
        steps = data.get("steps") if isinstance(data.get("steps"), list) else []
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
        handoff = extra.get("handoff") if isinstance(extra.get("handoff"), dict) else {}
        created = str(handoff.get("created_at") or "")
        last = str(handoff.get("last_activity") or "")
        if steps:
            first_ts = steps[0].get("timestamp") if isinstance(steps[0], dict) else None
            last_ts = steps[-1].get("timestamp") if isinstance(steps[-1], dict) else None
            if not created and isinstance(first_ts, str):
                created = first_ts
            if not last and isinstance(last_ts, str):
                last = last_ts
        last = last or _mtime_iso(path)
        created = created or last

        title = None
        for step in steps:
            if isinstance(step, dict) and step.get("source") == "user":
                msg = step.get("message")
                if isinstance(msg, str) and msg.strip():
                    title = msg.strip()[:80]
                    break

        project_cwd = handoff.get("cwd") or (str(cwd.resolve()) if cwd else None)
        return SessionRef(
            session_id=session_id,
            path=path,
            cwd=project_cwd,
            created_at=created,
            last_activity=last,
            message_count=len(steps),
            title=title,
        )

    def extract(self, ref: SessionRef) -> CanonicalTranscript:
        with ref.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return from_atif(
            data,
            source_path=str(ref.path),
            default_cwd=ref.cwd or "",
        )


class AtifInjector(Injector):
    agent_name = "atif"

    def inject(self, transcript: CanonicalTranscript) -> Path:
        cwd = transcript.metadata.cwd or str(Path.cwd())
        project_dir = self.home / encode_cwd(cwd)
        project_dir.mkdir(parents=True, exist_ok=True)
        session_id = transcript.metadata.session_id or str(uuid.uuid4())
        # Always mint a fresh file so we never overwrite a prior export.
        out_id = str(uuid.uuid4())
        path = project_dir / f"{out_id}.json"

        traj = to_atif(transcript)
        traj["session_id"] = session_id
        traj["trajectory_id"] = out_id
        # Re-stamp agent as handoff exporter while preserving source in extra.
        traj["agent"] = {
            "name": "handoff",
            "version": traj.get("agent", {}).get("version", "0.2.0"),
            **({"model_name": transcript.metadata.model} if transcript.metadata.model else {}),
        }
        problems = validate_atif(traj)
        if problems:
            # Still write — validate_atif is advisory for edge cases — but note them.
            traj.setdefault("notes", "")
            note = "handoff validation: " + "; ".join(problems)
            traj["notes"] = (traj["notes"] + "\n" + note).strip() if traj["notes"] else note

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(traj, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return path


def register() -> None:
    register_extractor("atif", lambda home: AtifExtractor(home))
    register_injector("atif", lambda home: AtifInjector(home))


register()
