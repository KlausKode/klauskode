# Klaus Kode

Donate your excess Claude Code credits to open source — Klaus Kode picks up a GitHub issue, writes a fix, self-reviews it, and pushes a branch to your fork with a one-click PR link.

As easy as:
```bash
./run.sh --repo owner/repo --issue 42
```

## How it works

Everything runs inside Docker — nothing is installed on your machine. The fork and branch are created under **your GitHub username**, so the PR comes from you. **No PR is ever created automatically** — you always review and click "Create pull request" yourself.

## Pipeline overview

```
 1.  Validate — check auth, repo, and issue
 2.  Fork & clone — fork the repo and clone it in Docker
 3.  Setup — read contributing guidelines, create feature branch
 4.  Implement — Claude works on the issue
 5.  Review — Claude self-reviews and fixes issues
 6.  Push — push branch to your fork
 7.  Link — print a clickable URL to open the PR
```

At the end, you get a URL. Click it, review the changes on GitHub, and hit "Create pull request" if you're happy.

## Prerequisites

- **Docker**
- **GitHub Personal Access Token** (`GH_TOKEN`) — see [setup instructions](#github-token-setup) below
- **Claude credentials** — either `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` — see [setup instructions](#claude-credentials-setup) below

## Quick start

```bash
cp .env.example .env
# Fill in your tokens in .env
set -a && source .env && set +a

./run.sh --repo owner/repo --issue 42
```

## Usage

```
./run.sh --repo owner/repo --issue <number> [options]
```

| Option | Description |
|---|---|
| `--repo owner/repo` | Target GitHub repository |
| `--issue N` | Issue number to work on |
| `-v` / `-vv` | Increase output verbosity |

Output is printed to the terminal and saved to `logs/run_<timestamp>.log`.

## GitHub token setup

1. Go to https://github.com/settings/tokens/new
2. Give it a name (e.g. "klaus-kode")
3. Set expiration as you see fit
4. Select the **`public_repo`** scope — this is the only scope needed
5. Click **Generate token**
6. Copy the token and set it as `GH_TOKEN`

The `public_repo` scope grants read/write access to public repositories. It **cannot** delete repos (that requires the separate `delete_repo` scope, which you should never enable for this).

### What the token is used for

Klaus Kode uses your GitHub token to:

- **Verify authentication** (read your username)
- **Fork** the target repo to your account
- **Clone** your fork
- **Push** a feature branch to your fork

That's it. It does **not**:

- Create or merge pull requests (you do that by clicking the link)
- Delete any repositories, branches, or data
- Access private repositories (unless your token explicitly allows it)
- Modify repository settings
- Manage collaborators or permissions

### Security notes

- The token is passed into the Docker container via environment variable and never written to disk or logged.
- The container is ephemeral (`--rm`) — it is destroyed after each run.
- All git operations use `gh auth setup-git` for credential handling — the token is never embedded in clone URLs.

## Claude credentials setup

You need **one** of the following:

### Option A: API key (`ANTHROPIC_API_KEY`)

1. Go to https://console.anthropic.com/settings/keys
2. Click **Create Key**
3. Copy the key and set it as `ANTHROPIC_API_KEY`

This requires an Anthropic account with API credits.

### Option B: OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`)

1. Install the Claude CLI (`npm install -g @anthropic-ai/claude-code`)
2. Run `claude login` and complete the browser-based login
3. Copy the token from `~/.claude/credentials.json` and set it as `CLAUDE_CODE_OAUTH_TOKEN`

This uses your Claude Pro/Team subscription instead of API credits.

## What can go wrong

Be aware of these risks before running:

- **Bad code gets pushed to your fork.** Claude might write incorrect, incomplete, or subtly broken code. Your fork will have a branch with this code. You can always delete the branch.
- **The PR link pre-fills a description.** If you click the link without reviewing, you might open a low-quality PR on someone else's repo. Always review before submitting.

Klaus Kode **cannot** delete repos, modify repo settings, access your private repos (with the recommended token setup), or take any action outside of forking, cloning, and pushing a branch.

## Contact

Questions, complaints, praise, or opt-out requests: **klauskode@protonmail.com**

## Disclaimer

THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED. USE AT YOUR OWN RISK. The author guarantees nothing — not correctness, not fitness for any purpose, not happy linters. If you accidentally trigger the AI apocalypse and I have to starve surrounded by shiny new paperclips I will be upset. You are solely responsible for reviewing code changes before opening a PR against the upstream.


Klaus Kode is an independent project. It is **not affiliated with, endorsed by, or sponsored by Anthropic or Claude Code**.
