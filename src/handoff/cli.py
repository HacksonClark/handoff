"""handoff command-line entry point."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from handoff import __version__
from handoff.agents import get_extractor, get_injector
from handoff.agents.base import known_agents
from handoff.canonical import CanonicalTranscript
from handoff.config import Config, ensure_config, load_config
from handoff.formatters import to_json, to_markdown
from handoff.redact import Redactor

AGENT_ALIASES = {
    "cc": "claude",
    "claude-code": "claude",
}


def _canon_agent(name: str) -> str:
    name = name.strip().lower()
    return AGENT_ALIASES.get(name, name)


def _success(msg: str) -> None:
    click.echo(f"✓ {msg}")


def _info(msg: str) -> None:
    click.echo(msg)


def _die(msg: str, code: int = 1) -> None:
    click.echo(click.style(f"✗ {msg}", fg="red"), err=True)
    sys.exit(code)


def _format_duration(start: str, end: str) -> str:
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        secs = int((e - s).total_seconds())
        if secs < 60:
            return f"{secs}s"
        mins, s2 = divmod(secs, 60)
        if mins < 60:
            return f"{mins}m {s2}s"
        hrs, mins = divmod(mins, 60)
        return f"{hrs}h {mins}m"
    except (ValueError, AttributeError):
        return ""


def _relative(ts: str) -> str:
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - t
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except (ValueError, AttributeError):
        return ts


def _short_id(sid: str) -> str:
    return sid[:8] if sid else "?"


class HandoffGroup(click.Group):
    """Route ``handoff <from-agent> <to-agent>`` to the ``transfer`` subcommand."""

    def resolve_command(self, ctx, args):
        if args:
            first = args[0]
            # If first token is not a registered subcommand but looks like an
            # agent name, treat it as "handoff transfer <from> <to> ..."
            if first not in self.commands and not first.startswith("-"):
                candidate = _canon_agent(first)
                if candidate in known_agents() or candidate in AGENT_ALIASES.values():
                    args = ["transfer", *args]
        return super().resolve_command(ctx, args)


@click.group(
    cls=HandoffGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "handoff: move conversation context between AI coding agents.\n\n"
        "Usage:\n\n"
        "    handoff <from-agent> <to-agent>   # e.g. handoff codex claude\n"
        "    handoff list                       # sessions for this project\n"
        "    handoff status                     # per-agent summary\n"
        "    handoff config                     # show/edit configuration"
    ),
)
@click.version_option(__version__, prog_name="handoff")
def main() -> None:
    pass


# ---------------------------------------------------------------------------
# transfer
# ---------------------------------------------------------------------------


@main.command("transfer")
@click.argument("from_agent")
@click.argument("to_agent")
@click.option("--session-id", help="Source session id (defaults to most recent).")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "json", "native"]),
    default=None,
    help="Output format when printing (used with --dry-run or --no-inject).",
)
@click.option("--dry-run", is_flag=True, help="Show what would be transferred without writing.")
@click.option(
    "--no-inject", is_flag=True, help="Print the transcript to stdout instead of writing."
)
@click.option(
    "--redact-secrets/--no-redact-secrets",
    default=None,
    help="Redact API keys and tokens from the transcript.",
)
@click.option(
    "--cwd",
    "cwd_opt",
    type=click.Path(file_okay=False, path_type=Path),
    help="Project directory (defaults to $PWD).",
)
def transfer_cmd(
    from_agent: str,
    to_agent: str,
    session_id: str | None,
    fmt: str | None,
    dry_run: bool,
    no_inject: bool,
    redact_secrets: bool | None,
    cwd_opt: Path | None,
) -> None:
    """Transfer session context between agents: ``handoff codex claude``."""
    from_a = _canon_agent(from_agent)
    to_a = _canon_agent(to_agent)

    if from_a == to_a:
        _die(f"from-agent and to-agent are the same: {from_a!r}")

    cfg = load_config()
    project = (cwd_opt or Path.cwd()).resolve()

    known = set(known_agents())
    if from_a not in known:
        _die(f"unknown source agent {from_a!r} (known: {sorted(known)})")
    if to_a not in known:
        _die(f"unknown target agent {to_a!r} (known: {sorted(known)})")

    try:
        extractor = get_extractor(from_a, cfg.home_for(from_a))
    except (KeyError, ValueError) as e:
        _die(str(e))
        return

    if session_id:
        ref = extractor.find_by_id(session_id, project) or extractor.find_by_id(session_id)
        if not ref:
            _die(f"no {from_a} session with id starting with {session_id!r}")
            return
    else:
        ref = extractor.find_latest(project)
        if not ref:
            _die(f"no {from_a} sessions found for project {project}")
            return

    duration = _format_duration(ref.created_at, ref.last_activity)
    suffix = f", {duration}" if duration else ""
    _success(
        f"Found {from_a.title()} session {_short_id(ref.session_id)} "
        f"({ref.message_count} messages{suffix})"
    )

    transcript = extractor.extract(ref)
    _success("Extracted context")

    redact = cfg.redact_secrets if redact_secrets is None else redact_secrets
    if redact:
        Redactor(patterns=cfg.redaction_patterns, enabled=cfg.redaction_enabled).redact_transcript(
            transcript
        )

    output_format = fmt or cfg.default_format

    if dry_run or no_inject:
        _emit(transcript, output_format)
        if dry_run:
            click.echo(f"\n(dry run — would inject into {to_a})", err=True)
        return

    try:
        injector = get_injector(to_a, cfg.home_for(to_a))
    except (KeyError, ValueError) as e:
        _die(str(e))
        return

    new_path = injector.inject(transcript)
    _success(f"Created {to_a.title()} session: {new_path.stem}")
    _info(f"  {new_path}")
    _info("Ready to continue!")


def _emit(transcript: CanonicalTranscript, fmt: str) -> None:
    if fmt == "json":
        click.echo(to_json(transcript))
    elif fmt == "markdown":
        click.echo(to_markdown(transcript))
    else:
        # "native" without a target; markdown is a sensible default for humans
        click.echo(to_markdown(transcript))


# ---------------------------------------------------------------------------
# list / status / config
# ---------------------------------------------------------------------------


@main.command("list")
@click.option("--agent", help="Only list sessions for this agent.")
@click.option(
    "--all",
    "all_projects",
    is_flag=True,
    help="Show sessions from every project, not just this one.",
)
@click.option(
    "--cwd",
    "cwd_opt",
    type=click.Path(file_okay=False, path_type=Path),
    help="Project directory (defaults to $PWD).",
)
def list_cmd(agent: str | None, all_projects: bool, cwd_opt: Path | None) -> None:
    """List available sessions for the current project."""
    cfg = load_config()
    project = None if all_projects else (cwd_opt or Path.cwd()).resolve()

    agents = [_canon_agent(agent)] if agent else known_agents()
    any_found = False
    for a in agents:
        try:
            extractor = get_extractor(a, cfg.home_for(a))
        except (KeyError, ValueError):
            continue
        refs = extractor.list_sessions(project)
        label = a.title() + (" sessions" if len(refs) != 1 else " session")
        where = str(project) if project else "(all projects)"
        click.echo(click.style(f"\n{label} in {where}:", bold=True))
        if not refs:
            click.echo("  (none)")
            continue
        any_found = True
        for r in refs[:20]:
            title = r.title or ""
            when = r.last_activity[:19] if r.last_activity else ""
            click.echo(
                f"  {_short_id(r.session_id)}  | {when}  | {r.message_count:>4} msgs  | {title}"
            )
        if len(refs) > 20:
            click.echo(f"  ... and {len(refs) - 20} more")
    if not any_found and project is not None:
        click.echo("\nNo sessions found. Try --all to see sessions from other projects.")


@main.command("status")
@click.option(
    "--cwd",
    "cwd_opt",
    type=click.Path(file_okay=False, path_type=Path),
    help="Project directory (defaults to $PWD).",
)
def status_cmd(cwd_opt: Path | None) -> None:
    """Show current project and latest session per agent."""
    cfg = load_config()
    project = (cwd_opt or Path.cwd()).resolve()
    click.echo(click.style(f"Project: {project}", bold=True))
    for a in known_agents():
        try:
            extractor = get_extractor(a, cfg.home_for(a))
        except (KeyError, ValueError):
            continue
        latest = extractor.find_latest(project)
        if latest:
            rel = _relative(latest.last_activity) if latest.last_activity else ""
            click.echo(
                f"  {a.title():<12} {_short_id(latest.session_id)} "
                f"({latest.message_count} messages, {rel})"
            )
        else:
            click.echo(f"  {a.title():<12} (no sessions)")
    click.echo("\nReady to handoff between any of the above.")


@main.command("config")
@click.option("--edit", is_flag=True, help="Open ~/.handoff/config.toml in $EDITOR.")
@click.option("--path", "show_path", is_flag=True, help="Just print the config path.")
def config_cmd(edit: bool, show_path: bool) -> None:
    """View or edit handoff configuration."""
    path = ensure_config()
    if show_path:
        click.echo(path)
        return
    if edit:
        click.edit(filename=str(path))
        return
    cfg: Config = load_config(path)
    click.echo(click.style(f"Config: {path}", bold=True))
    click.echo(f"  claude_home    = {cfg.claude_home}")
    click.echo(f"  codex_home     = {cfg.codex_home}")
    click.echo(f"  opencode_home  = {cfg.opencode_home}")
    click.echo(f"  redact_secrets = {cfg.redact_secrets}")
    click.echo(f"  auto_inject    = {cfg.auto_inject}")
    click.echo(f"  default_format = {cfg.default_format}")
    click.echo(f"\nRegistered agents: {', '.join(known_agents())}")


# ---------------------------------------------------------------------------
# agents / completion
# ---------------------------------------------------------------------------


@main.command("agents")
def agents_cmd() -> None:
    """List registered agent adapters (built-in + plugins)."""
    for a in known_agents():
        click.echo(a)


_COMPLETION_INSTRUCTIONS = {
    "bash": ('# Add to ~/.bashrc:\neval "$(_HANDOFF_COMPLETE=bash_source handoff)"\n'),
    "zsh": ('# Add to ~/.zshrc:\neval "$(_HANDOFF_COMPLETE=zsh_source handoff)"\n'),
    "fish": (
        "# Add to ~/.config/fish/completions/handoff.fish:\n"
        "_HANDOFF_COMPLETE=fish_source handoff | source\n"
    ),
}


@main.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.option(
    "--install", is_flag=True, help="Print the install instructions instead of the script."
)
def completion_cmd(shell: str, install: bool) -> None:
    """Print a shell-completion script for the chosen shell.

    Pipe it into your shell config, or run with ``--install`` to see the
    one-liner that enables completion dynamically.
    """
    if install:
        click.echo(_COMPLETION_INSTRUCTIONS[shell])
        return

    import os
    import subprocess

    env = os.environ.copy()
    env["_HANDOFF_COMPLETE"] = f"{shell}_source"
    try:
        out = subprocess.run(
            ["handoff"], env=env, check=False, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _die(f"failed to generate completion script: {exc}")
        return
    click.echo(out.stdout, nl=False)


if __name__ == "__main__":
    main()
