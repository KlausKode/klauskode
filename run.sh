#!/usr/bin/env bash
set -euo pipefail

IMAGE="klaus-kode"
LOG_DIR="logs"

# --- Usage ---
# Mode 1: explicit repo + explicit issue
#   ./run.sh --repo owner/repo --issue 42 [-v]
#
# Mode 2: explicit repo + Claude picks issue
#   ./run.sh --repo owner/repo --find "medium difficulty bug fix" [-v]
#
# Mode 3: explicit repo + Klaus defaults to easy issue
#   ./run.sh --repo owner/repo [-v]
#
# Mode 4: Klaus finds repo AND picks issue
#   ./run.sh --find-repo "python web framework" [-v]
#   ./run.sh --find-repo "python web framework" --find "documentation fix" [-v]
#
# --repo and --find-repo are mutually exclusive; one is required.
# --issue and --find are mutually exclusive and optional.
# --find-repo + --issue is an error.
# When neither --issue nor --find is given, defaults to easy beginner-friendly issues.
#
# Output is shown in the terminal AND saved to logs/run_<timestamp>.log
#
# Required env vars:
#   GH_TOKEN               GitHub PAT (classic with public_repo, or fine-grained with Contents + PRs read/write)
#   ANTHROPIC_API_KEY       Anthropic API key  (or CLAUDE_CODE_OAUTH_TOKEN)

if [ -z "${GH_TOKEN:-}" ]; then
  echo "Error: GH_TOKEN is not set." >&2
  exit 1
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "Error: Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN." >&2
  exit 1
fi

mkdir -p "$LOG_DIR" && chmod 777 "$LOG_DIR"
LOGFILE="${LOG_DIR}/run_$(date +%Y%m%d_%H%M%S).log"
echo "==> Logging to ${LOGFILE}"

{
  echo "==> Building Docker image '${IMAGE}'..."
  docker build -t "$IMAGE" .

  echo "==> Running klaus-kode..."
  docker run --rm \
    -v "$(pwd)/logs:/workspace/logs" \
    -e GH_TOKEN \
    -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    -e CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" \
    "$IMAGE" \
    "$@"
} 2>&1 | tee "$LOGFILE"
