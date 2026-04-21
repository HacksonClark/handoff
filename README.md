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

## Install

```bash
# uv (recommended)
uv tool install handoff

# pip
pip install handoff
```

## Usage

```bash
# Transfer the most recent Codex session for this project into Claude Code
handoff codex claude

# Reverse it
handoff claude codex

# Three-way — OpenCode is also supported
handoff codex opencode
handoff opencode claude

# See what would happen, don't touch anything
handoff codex claude --dry-run --format markdown

# Extract only, print to stdout (great for piping into LLM review)
handoff codex claude --no-inject > /tmp/session.md

# Pick a specific source session
handoff codex claude --session-id 019cdd7f
```

### Other commands

```bash
handoff list                 # sessions available for the current project
handoff list --agent codex   # filtered by agent
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

Cursor and GitHub Copilot Chat are on the roadmap — they live inside VS Code's SQLite state, which needs a different adapter approach.

## Configuration

`~/.handoff/config.toml` is created with defaults on first run:

```toml
[agents]
claude_home = "~/.claude"
codex_home = "~/.codex"
# opencode follows XDG_DATA_HOME; override below if yours lives somewhere else.
# opencode_home = "~/.local/share/opencode"

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

Redaction is **best-effort** — the default patterns cover the common API-key shapes (OpenAI, Anthropic, GitHub, AWS, Bearer, PEM) but you should still review transcripts before sharing them externally.

## Plugins

Add support for a new agent by publishing a package that registers an extractor/injector under the `handoff.agents` entry-point group:

```toml
# your_package/pyproject.toml
[project.entry-points."handoff.agents"]
aider = "your_package.aider:register"
```

```python
# your_package/aider.py
from handoff.agents.base import register_extractor, register_injector

def register() -> None:
    register_extractor("aider", lambda home: AiderExtractor(home))
    register_injector("aider", lambda home: AiderInjector(home))
```

Install the plugin alongside handoff and `handoff aider claude` will just work.

See `src/handoff/agents/opencode.py` for a complete, real-world implementation.

## Safety

- **Non-destructive.** Source sessions are never modified.
- **Injected files are created with mode `0600`.**
- **Session IDs** are generated with `uuid4` / `secrets` — no collisions with source agents.
- **Path-traversal guards** on the Claude project-dir encoder.
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
