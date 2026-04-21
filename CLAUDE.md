# Handoff: Agent Context Transfer CLI

## Project Overview

**Handoff** is a command-line tool that transfers conversation context from one AI coding agent to another. Simply run `handoff <from-agent> <to-agent>` in your project directory to seamlessly continue work in a different agent.

**Problem:** When you switch agents mid-project (due to rate limits, needing a second opinion, or tool preference), you lose conversation history and must re-explain context.

**Solution:** Handoff extracts transcripts from one agent's home directory and injects them into another, allowing you to pick up exactly where you left off.

---

## Core Goals

1. **Frictionless handoff** — Single command to transfer all context
2. **Automatic discovery** — Detect current project and find relevant sessions
3. **Format preservation** — Maintain conversation structure, tool outputs, diffs
4. **Agent-agnostic** — Support multiple agents (Claude Code, Codex, OpenCode, etc.)
5. **Non-destructive** — Never modify source agent files
6. **Reversible** — Transfer bidirectionally between any supported agents

---

## Supported Agents (MVP)

### Phase 1 (MVP)
- **Claude Code** — Read/write from `~/.claude/`
- **OpenAI Codex** — Read/write from `~/.codex/`

### Phase 2 (Future)
- **OpenCode** — Read/write from `~/.opencode/`
- **GitHub Copilot** — Where applicable
- **Cursor** — Editor-based context
- Custom agents via plugin interface

---

## Command Structure

### Primary Command: Simple Agent-to-Agent Transfer

#### `handoff <from-agent> <to-agent>`

Transfer conversation context from one agent to another for the current project.

```bash
handoff claude codex      # Transfer Claude Code context to Codex
handoff codex claude      # Transfer Codex context to Claude Code
handoff codex opencode    # Transfer Codex context to OpenCode
```

**How it works:**
1. Detects current working directory (project root)
2. Finds the most recent session for `<from-agent>` in `~/.{from-agent}/`
3. Parses the session transcript (JSONL format)
4. Normalizes to canonical format
5. Translates to target agent's native format
6. Writes to `~/.{to-agent}/sessions/` directory
7. Outputs success message with new session ID

**Options:**
- `--session-id <ID>` — Specify which session to transfer (defaults to most recent)
- `--format markdown|json|native` — Output format for review (doesn't inject)
- `--dry-run` — Show what would be transferred without modifying target
- `--redact-secrets` — Remove API keys, tokens, env vars (default: true)
- `--no-inject` — Extract only, print to stdout instead of writing to target

**Examples:**
```bash
# In ~/my-app directory, transfer latest Codex session to Claude Code
$ handoff codex claude
✓ Found Codex session (42 messages, 1h 23m)
✓ Extracted context
✓ Created Claude Code session: claude-session-xyz
✓ Ready to continue!

# Review what will be transferred before injecting
$ handoff codex claude --dry-run --format markdown

# Extract without injecting (for manual review/editing)
$ handoff codex claude --no-inject > /tmp/session.md

# Transfer a specific session by ID
$ handoff codex claude --session-id abc123def
```

### Optional Secondary Commands

#### `handoff list [--agent <agent>]`
List available sessions for the current project.

```bash
handoff list              # Show all sessions for current project
handoff list --agent codex    # Show only Codex sessions
```

**Output:**
```
Claude Code sessions in ~/my-app:
  session-123  | 2025-04-21 10:30 | 42 messages | Implement auth module
  session-456  | 2025-04-20 14:00 | 28 messages | Fix race condition

Codex sessions in ~/my-app:
  session-789  | 2025-04-21 11:45 | 35 messages | Add tests
  session-000  | 2025-04-20 09:15 | 19 messages | Refactor API
```

#### `handoff status`
Show current project and available agent sessions.

```bash
handoff status
```

**Output:**
```
Project: ~/my-app
  Claude Code: session-123 (42 messages, 1h 23m ago)
  Codex:       session-789 (35 messages, now)
  OpenCode:    (no sessions)

Ready to handoff between any agents.
```

#### `handoff config`
View/configure agent home directories.

```bash
handoff config              # Show current configuration
handoff config --edit       # Edit ~/.handoff/config.toml
```

**Default config** (auto-detected):
```toml
[agents]
claude_home = "~/.claude"
codex_home = "~/.codex"
opencode_home = "~/.opencode"
```

---

## Architecture

### Core Components

1. **Session Discoverer**
   - Scan agent home directories for session files
   - Match sessions to current project directory
   - Parse metadata (timestamp, message count, status)

2. **Extractors** — Agent-specific readers
   - `ClaudeExtractor` — Parse Claude Code transcript format
   - `CodexExtractor` — Parse Codex JSONL rollout files
   - `OpenCodeExtractor` — Parse OpenCode sessions

3. **Normalizer** — Convert all formats to canonical model
   - Unified transcript: messages, tool calls, outputs, artifacts
   - Metadata: timestamps, author, message type, tool use
   - Files modified and diffs

4. **Injectors** — Agent-specific writers
   - `ClaudeInjector` — Write to `~/.claude/sessions/`
   - `CodexInjector` — Write to `~/.codex/sessions/` as valid JSONL
   - `OpenCodeInjector` — Write to `~/.opencode/sessions/`

5. **Formatters** — Output generators
   - `MarkdownFormatter` — Human-readable summary + full transcript
   - `JSONFormatter` — Structured canonical format
   - `NativeFormatter` — Agent-specific injection format

### Data Flow

```
handoff codex claude
    ↓
Discover sessions in ~/.codex/ matching current project
    ↓
Find most recent session → read rollout-*.jsonl
    ↓
Parse JSONL, extract messages and metadata
    ↓
Normalize to canonical transcript format
    ↓
Translate to Claude Code native format
    ↓
Create new session file in ~/.claude/sessions/YYYY/MM/DD/
    ↓
Output: "✓ Created Claude Code session: session-xyz"
```

---

## Data Model

### Canonical Transcript Format

```json
{
  "metadata": {
    "session_id": "uuid",
    "source_agent": "codex|claude",
    "source_session_path": "/path/to/rollout-*.jsonl",
    "created_at": "ISO8601",
    "last_activity": "ISO8601",
    "message_count": 42,
    "cwd": "/path/to/project"
  },
  "transcript": [
    {
      "id": "msg-1",
      "timestamp": "ISO8601",
      "author": "user|agent",
      "type": "message|tool_call|tool_result|approval",
      "content": "string",
      "metadata": {
        "model": "optional",
        "tool_name": "optional"
      }
    }
  ],
  "artifacts": {
    "files_modified": ["file1.ts", "file2.js"],
    "diffs": [
      {
        "file": "file1.ts",
        "before": "code snippet",
        "after": "code snippet"
      }
    ]
  }
}
```

---

## User Workflows

### Workflow 1: Hit Rate Limit, Switch Agents
```bash
# Working in Codex, hit rate limit
$ codex
(rate limited...)
$ exit

# In a new terminal, same project directory
$ handoff codex claude
✓ Found Codex session (42 messages)
✓ Created Claude Code session: claude-xyz

# Start Claude Code with the context
$ claude
(picks up where you left off)
```

### Workflow 2: Get a Second Opinion
```bash
# Codex completed a task, export for review in Claude Code
$ handoff codex claude --dry-run --format markdown > /tmp/review.md
$ cat /tmp/review.md

# If happy with it, inject
$ handoff codex claude --session-id abc123
```

### Workflow 3: Cross-Agent Comparison
```bash
# See what sessions are available
$ handoff list
Claude Code: session-123 (42 messages)
Codex:       session-456 (38 messages)

# Compare both approaches
$ handoff list --agent claude    # See Claude approach
$ handoff list --agent codex     # See Codex approach

# Continue with the one you prefer
$ handoff codex claude  # Switch to Claude if Codex approach is better
```

---

## Configuration

### `~/.handoff/config.toml`

```toml
# Agent home directories (auto-detected by default)
[agents]
claude_home = "~/.claude"
codex_home = "~/.codex"
opencode_home = "~/.opencode"

# Default behavior
[defaults]
redact_secrets = true
auto_inject = true
format = "native"  # or "markdown", "json"

# Patterns to redact from transcripts
[redaction]
enabled = true
patterns = [
  "OPENAI_API_KEY=.*",
  "ANTHROPIC_API_KEY=.*",
  "Bearer [a-zA-Z0-9_\\-\\.]+",
  "sk-[a-zA-Z0-9]+"
]
```

---

## Implementation Phases

### Phase 1: MVP (Codex → Claude Code)
- [ ] Parse Codex JSONL format (`rollout-*.jsonl`)
- [ ] Discover sessions by project directory
- [ ] Normalize to canonical format
- [ ] Inject into Claude Code sessions
- [ ] Basic `handoff codex claude` command
- [ ] `--dry-run` and `--format` options
- [ ] Redact secrets
- [ ] Config discovery

**Deliverable:** One command that works: `handoff codex claude`

### Phase 2: Claude Code Support & Bidirectionality
- [ ] Parse Claude Code session format
- [ ] Bidirectional transfer (claude → codex)
- [ ] `handoff list` command
- [ ] `handoff status` command
- [ ] Session selection by ID
- [ ] Comprehensive tests

### Phase 3: Extended Agent Support
- [ ] OpenCode extractor/injector
- [ ] GitHub Copilot integration
- [ ] Plugin system for custom agents

### Phase 4: Polish & Distribution
- [ ] Comprehensive test suite
- [ ] Performance optimization
- [ ] Man pages / help
- [ ] Homebrew/npm/cargo distribution
- [ ] Security audit

---

## Technical Considerations

### File Formats
- **Codex:** JSONL streaming format (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`)
- **Claude Code:** TBD (need to inspect `~/.claude/`)
- **OpenCode:** TBD

### Session Matching
- Sessions stored by date hierarchy
- Match to project by comparing file paths in context
- Handle projects with multiple sessions per day

### Writing New Sessions
- Create session file with proper naming convention
- Ensure JSONL format is valid
- Include all required metadata fields
- Preserve timestamps and authorship

### Security
- Never write credentials to transcripts
- Redact API keys by default (configurable)
- Warn before transferring potentially sensitive code
- Validate session files before injection

### Error Handling
- Graceful fallback if agent home not found
- Handle corrupted session files
- Skip unparseable lines in JSONL
- Clear error messages for common issues

---

## Testing Strategy

### Unit Tests
- JSONL parsing (Codex format)
- Canonical format normalization
- Secret redaction
- Config loading

### Integration Tests
- Extract real Codex session
- Inject into Claude Code
- Session resumption
- Bidirectional transfer (codex → claude → codex)

### Manual Testing
- macOS, Linux, Windows
- Real agent workflows
- Various session sizes
- Different project structures

---

## Questions & Unknowns

1. **Claude Code session format** — Where does it store transcripts? Format?
2. **Project detection** — How to reliably match session to current project?
3. **Session file naming** — Do we generate new IDs or preserve source IDs?
4. **Approval state** — Should we preserve user approval decisions?
5. **Tool outputs** — How much context from tool results should we preserve?

---

## Success Criteria

- ✅ One command works: `handoff codex claude`
- ✅ Preserves conversation history without data loss
- ✅ Zero friction (user types one command in their project dir)
- ✅ Works on macOS, Linux, Windows
- ✅ Handles >1000-message sessions efficiently
- ✅ Redacts secrets correctly
- ✅ Non-destructive (original session untouched)
- ✅ Community uptake: 100+ GitHub stars

---

## Name & Branding

**Handoff** — Simple verb. Implies smooth, intentional context passing.

**Tagline:** *"Seamlessly switch between AI agents without losing context."*

**Usage Feel:**
```
$ handoff codex claude
(done, pick up in Claude Code)
```

Quick, natural, one word = one action.
