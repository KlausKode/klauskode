#!/usr/bin/env bash
set -euo pipefail

IMAGE="klaus-kode"
LOG_DIR="logs"

# --- Usage ---
# ./run.sh --repo owner/repo --issue 42 [-v]
# ./run.sh --repo owner/repo --find "easy" [-v]
# ./run.sh --repo owner/repo --find "simple documentation fix" [-v]
#
# --issue and --find are mutually exclusive; one is required.
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

mkdir -p "$LOG_DIR"
LOGFILE="${LOG_DIR}/run_$(date +%Y%m%d_%H%M%S).log"
echo "==> Logging to ${LOGFILE}"

{
  echo "==> Building Docker image '${IMAGE}'..."
  docker build -t "$IMAGE" .

  echo "==> Running klaus-kode..."
  docker run --rm \
    -e GH_TOKEN \
    -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    -e CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}" \
    "$IMAGE" \
    "$@"
} 2>&1 | tee "$LOGFILE"
