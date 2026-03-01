# Klaus Kode

Automated tool that donates excess Claude credits to open source by solving GitHub issues.

## Architecture
- `run.sh` — Bash entry point; validates env, builds Docker, runs CLI
- `klaus_kode/cli.py` — Main pipeline orchestrator (arg parsing → issue selection → fork → clone → work → review → push)
- `klaus_kode/claude_runner.py` — All Claude Code interactions (SDK calls, streaming TUI, prompts)
- `klaus_kode/github.py` — GitHub API via `gh` CLI (fork, issues, repos, auth)
- `klaus_kode/run_logger.py` — Structured JSONL logging
- `klaus_kode/pr_template.py` — Fallback PR description templates
- `Dockerfile` — Container with Claude CLI, gh, git, python3

## Development Setup
- Use `uv` for all Python package management (not pip)
- Local venv: `uv venv .venv && source .venv/bin/activate`
- Install deps: `uv pip install claude-agent-sdk`
- Install project: `uv pip install -e .`

## Running
./run.sh --repo owner/repo --find "easy" -vv

## Key Conventions
- Use `uv` for all pip/venv operations — never use bare `pip`
- All Claude invocations go through `claude-agent-sdk` (Python async)
- All subprocess calls to git/gh are in `github.py` or `claude_runner.py`
- Sync wrappers around async SDK calls (cli.py stays synchronous)
- Structured JSONL logging for all steps
- Runtime deps inside Docker: git, gh, claude CLI, claude-agent-sdk

## Testing
Run manually with a test repo:
./run.sh --repo <your-fork-of-a-test-repo> --issue <number> -vv
Check logs/ for JSONL output.
