# Klaus Kode

Automated tool that donates excess Claude credits to open source by solving GitHub issues.

## Architecture

```
klaus_kode/
    __init__.py           # Package metadata
    cli.py                # Step-based pipeline orchestrator with PipelineContext
    context.py            # PipelineContext dataclass + Session persistence
    prompts.py            # System prompts, prompt builders, tool permission lists
    tui.py                # Spinner, colors, formatting helpers
    claude_sdk.py         # Claude SDK wrappers (_quick_claude, streaming TUI)
    repo_ops.py           # Git/repo operations (clone, branch, commit, push)
    selection.py          # AI selection (pick_issue, pick_repo, branch name, compliance)
    pr_description.py     # PR description generation, show_changes, review, save
    claude_runner.py      # FACADE: thin re-exports for backwards compat (will be removed)
    github.py             # GitHub API via `gh` CLI (fork, issues, repos, auth)
    run_logger.py         # Structured JSONL logging
    pr_template.py        # Fallback PR description templates
```

Supporting files:
- `run.sh` — Bash entry point; validates env, builds Docker, runs CLI
- `Dockerfile` — Container with Claude CLI, gh, git, python3

### Module dependency graph (no circular deps)

```
tui.py           -> (nothing)
prompts.py       -> github (Issue type)
claude_sdk.py    -> tui, prompts, claude_agent_sdk
selection.py     -> github, claude_sdk
repo_ops.py      -> github (Issue type), run_logger
pr_description.py -> claude_sdk, github, pr_template, prompts, repo_ops
context.py       -> github, run_logger
cli.py           -> context, github, run_logger (lazy: selection, repo_ops, claude_sdk, prompts, pr_description)
claude_runner.py -> (re-exports from all above)
```

### Pipeline (cli.py)

The pipeline is a sequence of named steps, each taking a `PipelineContext`:

1. `check_prerequisites` — Verify GitHub & Claude auth
2. `find_repo` — Search/validate target repository
3. `find_issue` — Fetch or AI-select an issue
4. `fork_and_clone` — Fork, clone, read contributing guidelines
5. `prepare_branch` — Suggest branch name, compliance check, create branch
6. `run_work` — Claude implements the fix
7. `review_and_push` — Self-review, generate PR description, push

Steps are resumable via `Session` (tracks completed steps in `/workspace/session.json`).

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
- Git/gh subprocess calls are in `repo_ops.py` and `github.py`
- Sync wrappers around async SDK calls (cli.py stays synchronous)
- All pipeline state lives in `PipelineContext` — no module-level mutable globals
- Structured JSONL logging for all steps via `RunLogger`
- Runtime deps inside Docker: git, gh, claude CLI, claude-agent-sdk

## Testing
Run manually with a test repo:
./run.sh --repo <your-fork-of-a-test-repo> --issue <number> -vv
Check logs/ for JSONL output.
