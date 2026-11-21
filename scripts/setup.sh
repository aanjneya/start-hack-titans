#!/usr/bin/env bash
# 60-second hackathon onboarding for aanjneya, daksh, jivi, tim.
# Designed to be run either manually or automatically by a coding agent on first session.
#
# Usage:
#   ./scripts/setup.sh [aanjneya|daksh|jivi|tim]
#   ./scripts/setup.sh --check   # exits 0 if this clone is already configured, 1 otherwise

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

is_valid_id() {
  case "$1" in
    aanjneya|daksh|jivi|tim) return 0 ;;
    *) return 1 ;;
  esac
}

# Fast health check for agents to detect fresh clones
if [[ "${1:-}" == "--check" ]]; then
  CFG_ID="$(git config --get hackathon.teammate 2>/dev/null || true)"
  HOOKS_PATH="$(git config --get core.hooksPath 2>/dev/null || true)"
  if is_valid_id "$CFG_ID" && [[ "$HOOKS_PATH" == ".githooks" && -f ".env" ]]; then
    echo "Configured for $CFG_ID (hooks=.githooks, .env present)."
    exit 0
  fi
  echo "Not configured yet (teammate='${CFG_ID:-unset}', hooksPath='${HOOKS_PATH:-unset}')."
  exit 1
fi

detect_teammate_id() {
  # 1. Existing local git config
  local cfg_id
  cfg_id="$(git config --get hackathon.teammate 2>/dev/null || true)"
  if is_valid_id "$cfg_id"; then
    echo "$cfg_id"
    return 0
  fi

  # 2. Current branch name
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if is_valid_id "$branch"; then
    echo "$branch"
    return 0
  fi

  # 3. Token-boundary match against git user.name, git user.email local-part, and OS username
  local g_name g_email g_email_local os_user raw_tokens
  g_name="$(git config --get user.name 2>/dev/null || true)"
  g_email="$(git config --get user.email 2>/dev/null || true)"
  g_email_local="${g_email%@*}"
  os_user="${USER:-$(whoami 2>/dev/null || true)}"

  raw_tokens="$(printf '%s %s %s\n' "$g_name" "$g_email_local" "$os_user" \
    | tr '[:upper:]' '[:lower:]' \
    | tr -cs 'a-z0-9' '\n')"

  local matched=""
  local count=0
  for candidate in aanjneya daksh jivi tim; do
    if printf '%s\n' "$raw_tokens" | grep -Eq "^${candidate}[0-9]*$"; then
      matched="$candidate"
      count=$((count + 1))
    fi
  done

  if (( count == 1 )); then
    echo "$matched"
    return 0
  fi
  return 1
}

USER_ID=""
if [[ $# -eq 1 ]]; then
  USER_ID="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  if ! is_valid_id "$USER_ID"; then
    echo "Error: <id> must be one of: aanjneya, daksh, jivi, tim (got '$1')" >&2
    exit 1
  fi
elif [[ $# -eq 0 ]]; then
  USER_ID="$(detect_teammate_id || true)"
  if ! is_valid_id "$USER_ID"; then
    echo "Could not auto-detect teammate ID from git config or username." >&2
    echo "Ask the user which teammate they are (aanjneya, daksh, jivi, tim) and run: ./scripts/setup.sh <id>" >&2
    exit 2
  fi
  echo "Auto-detected teammate ID: $USER_ID"
else
  echo "Usage: $0 [aanjneya|daksh|jivi|tim] | --check" >&2
  exit 1
fi

# 1. Save teammate ID locally so agents & brain-push.sh work with 0 human prompts
git config hackathon.teammate "$USER_ID"
echo "[1/5] Saved hackathon.teammate=$USER_ID in local git config."

# 2. Enable shared git hooks + executable bits
git config core.hooksPath .githooks
chmod +x .githooks/pre-push .githooks/commit-msg scripts/brain-push.sh scripts/verify.sh scripts/setup.sh
echo "[2/5] Enabled .githooks safety guardrails (pre-push + commit-msg)."

# 3. Verify agent rules & skill symlinks work on this OS
SYMLINK_OK=true
for skill_path in ".agent/skills/second-brain/SKILL.md" ".agents/skills/second-brain/SKILL.md" ".claude/skills/second-brain/SKILL.md"; do
  if [[ ! -f "$skill_path" ]]; then
    SYMLINK_OK=false
    echo "WARN: '$skill_path' is not readable (Windows symlink issue?)." >&2
  fi
done
if $SYMLINK_OK; then
  echo "[3/5] Verified AGENTS.md, CLAUDE.md, and second-brain skill links."
else
  echo "      Fix on Windows: enable Developer Mode, run 'git config core.symlinks true && git checkout -- .agents .claude'" >&2
fi

# 4. Initialize .env from .env.example if missing
if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
  echo "[4/5] Created .env from .env.example (MOCK_MODE=true)."
else
  echo "[4/5] .env already exists — kept untouched."
fi

# 5. Fetch origin (best-effort; works offline/sandboxed) and switch to personal branch
git fetch --quiet origin 2>/dev/null || true
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ "$CURRENT_BRANCH" == "$USER_ID" ]]; then
  echo "[5/5] Already on branch: $USER_ID"
elif git show-ref --verify --quiet "refs/heads/$USER_ID"; then
  if ! git checkout --quiet "$USER_ID"; then
    echo "WARN: Could not switch to existing branch '$USER_ID' due to uncommitted local changes. Commit/stash first, then run 'git checkout $USER_ID'." >&2
    exit 1
  fi
  echo "[5/5] Switched to existing branch: $USER_ID"
elif git show-ref --verify --quiet "refs/remotes/origin/$USER_ID"; then
  git checkout --quiet -b "$USER_ID" "origin/$USER_ID"
  echo "[5/5] Checked out remote branch: $USER_ID"
elif git show-ref --verify --quiet "refs/remotes/origin/main"; then
  git checkout --quiet -b "$USER_ID" origin/main
  echo "[5/5] Created branch '$USER_ID' from origin/main."
else
  git checkout --quiet -b "$USER_ID"
  echo "[5/5] Created branch '$USER_ID' from current HEAD."
fi

echo ""
echo "Ready to build, $USER_ID!"
echo "- Your coding agents will auto-sync .brain/ via ./scripts/brain-push.sh."
echo "- Tip: Click 'Always allow ./scripts/brain-push.sh' the first time your agent runs it (Claude Code is pre-configured in .claude/settings.json)."
