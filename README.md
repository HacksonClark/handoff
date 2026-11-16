# handoff

Seamlessly switch between AI coding agents without losing context.

```bash
$ handoff codex claude
✓ Found Codex session 019cdd7f (42 messages, 1h 23m)
✓ Extracted context
✓ Created Claude session: 4c9b7967-90d7-4fab-8e1d-6a95f1b3c8e2
  ~/.claude/projects/-Users-me-app/4c9b7967-....jsonl
Ready to continue!
```

## What it does

When you switch between AI coding agents mid-project — rate limits, a second opinion, tool preference — you normally lose conversation history. `handoff` reads the transcript from one agent's home directory and injects it into another, so you can pick up exactly where you left off.

Internally every session is normalized to a shared transcript model, and can be exported as **ATIF** (Agent Trajectory Interchange Format — the Harbor / Terminal-Bench standard) for portable interchange.

## Install

The CLI is named `handoff`; the PyPI distribution is `handoff-agent` (the
short name was already taken):

```bash
# uv (recommended)
uv tool install handoff-agent

# pip
pip install handoff-agent
```

## Usage

```bash
# Transfer the most recent Codex session for this project into Claude Code
handoff codex claude

# Reverse it
handoff claude codex

# Cursor ↔ others
handoff cursor claude
handoff codex cursor

# Gemini CLI, aider, OpenCode
handoff gemini claude
handoff aider codex
handoff codex opencode

# Neutral ATIF hop (Harbor-compatible trajectory JSON)
handoff cursor atif
handoff atif claude

# Export ATIF to stdout (great for Harbor trials / review)
handoff cursor claude --no-inject --format atif > trajectory.json

# See what would happen, don't touch anything
handoff codex claude --dry-run --format markdown

# Pick a specific source session
handoff codex claude --session-id 019cdd7f
```

### Other commands

```bash
handoff list                 # sessions available for the current project
handoff list --agent cursor  # filtered by agent
handoff list --all           # sessions across all projects

handoff status               # project + latest session per agent
handoff agents               # show registered adapters (built-in + plugins)

handoff config               # view configuration
handoff config --edit        # edit configuration

handoff completion zsh       # emit a zsh completion script
handoff completion zsh --install   # show install one-liner
```

## Supported agents

| Agent | Storage | Extract | Inject |
|---|---|---|---|
| **Claude Code** | `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` | ✓ | ✓ |
| **OpenAI Codex** | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | ✓ | ✓ |
| **OpenCode** | `~/.local/share/opencode/storage/` | ✓ | ✓ |
| **Cursor** | `~/.cursor/projects/<slug>/agent-transcripts/**/*.jsonl` | ✓ | ✓ |
| **Gemini CLI** | `~/.gemini/tmp/<hash>/chats/*.{json,jsonl}` | ✓ | ✓ |
| **aider** | `.aider.chat.history.md` (project-local) | ✓ | ✓ |
| **ATIF store** | `~/.handoff/trajectories/<encoded-cwd>/*.json` | ✓ | ✓ |

Cursor injection writes an Agents Window transcript on disk. The IDE sidebar chat list uses a separate SQLite index (`state.vscdb`) and may not show the new session — open the Agents Window to continue.

## Normalization & ATIF

Every extractor produces a **canonical transcript** (flat messages: user/agent text, reasoning, tool calls/results, artifacts). Injectors consume that shape.

For interchange with Harbor, Terminal-Bench, and other ATIF tooling, use:

```bash
# Print ATIF-v1.5 JSON
handoff codex claude --no-inject --format atif

# Persist into the built-in ATIF store, then re-inject elsewhere
handoff cursor atif
handoff atif claude --session-id <id>
```

ATIF maps cleanly onto handoff's model:

| ATIF | handoff canonical |
|---|---|
| `steps[].source: user/system` | `Message(author=user/system, type=message)` |
| `steps[].source: agent` + `message` / `reasoning_content` | agent `message` / `reasoning` |
| `steps[].tool_calls` | `type=tool_call` (+ metadata) |
| `steps[].observation.results` | `type=tool_result` |
| `extra.handoff.*` | cwd, files_modified, task_state, source path |

Harbor trial drops (`agent/trajectory.json`) are discovered automatically when you `handoff list --agent atif` / `handoff atif <agent>` from the trial directory.

## Configuration

`~/.handoff/config.toml` is created with defaults on first run:

```toml
[agents]
claude_home = "~/.claude"
codex_home = "~/.codex"
cursor_home = "~/.cursor"
gemini_home = "~/.gemini"
# opencode follows XDG_DATA_HOME; override below if yours lives somewhere else.
# opencode_home = "~/.local/share/opencode"
# atif_home = "~/.handoff/trajectories"

[defaults]
redact_secrets = true
auto_inject = true
format = "native"

[redaction]
enabled = true
patterns = [
    "OPENAI_API_KEY=.*",
    "ANTHROPIC_API_KEY=.*",
    "Bearer [a-zA-Z0-9_\\-\\.]{20,}",
    "sk-[a-zA-Z0-9]{20,}",
]
```

Any `{agent}_home` key is honored for plugins. Unknown agents fall back to `~/.{agent}`.

Redaction is **best-effort** — the default patterns cover the common API-key shapes (OpenAI, Anthropic, GitHub, AWS, Bearer, PEM) but you should still review transcripts before sharing them externally.

## Plugins

Add support for a new agent by publishing a package that registers an extractor/injector under the `handoff.agents` entry-point group:

```toml
# your_package/pyproject.toml
[project.entry-points."handoff.agents"]
mybot = "your_package.mybot:register"
```

```python
# your_package/mybot.py
from handoff.agents.base import register_extractor, register_injector

def register() -> None:
    register_extractor("mybot", lambda home: MyBotExtractor(home))
    register_injector("mybot", lambda home: MyBotInjector(home))
```

Install the plugin alongside handoff and `handoff mybot claude` will just work.
Optional: set `mybot_home = "..."` under `[agents]` in `~/.handoff/config.toml`.

See `src/handoff/agents/cursor.py` or `src/handoff/agents/opencode.py` for complete implementations.

## Safety

- **Non-destructive.** Source sessions are never modified.
- **Injected files are created with mode `0600`.**
- **Session IDs** are generated with `uuid4` / `secrets` — no collisions with source agents.
- **Path-traversal guards** on project-dir encoders.
- **Redaction** runs by default. Turn it off with `--no-redact-secrets` or `[defaults] redact_secrets = false`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv build                    # produces dist/*.whl + *.tar.gz
```

## License

MIT
