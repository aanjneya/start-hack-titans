#!/usr/bin/env bash
# Safely push ONLY .brain/ (and optionally challenge/) directly to origin/main
# using a temporary git worktree + 3-way per-file merge — never stashes or
# touches WIP feature code, and never silently overwrites teammates' edits.
#
# Usage:
#   ./scripts/brain-push.sh <aanjneya|daksh|jivi|tim> "<short summary>" [--include-challenge]
#   ./scripts/brain-push.sh "<short summary>" [--include-challenge]   # auto-detects ID from git config / branch

set -euo pipefail

usage() {
  echo "Usage: $0 [aanjneya|daksh|jivi|tim] \"<summary>\" [--include-challenge]" >&2
  exit 1
}

[[ $# -lt 1 ]] && usage

is_valid_id() {
  case "$1" in
    aanjneya|daksh|jivi|tim) return 0 ;;
    *) return 1 ;;
  esac
}

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

USER_ID=""
SUMMARY=""
INCLUDE_CHALLENGE=false

if is_valid_id "$1"; then
  USER_ID="$1"
  shift
else
  USER_ID="$(git config --get hackathon.teammate 2>/dev/null || true)"
  if ! is_valid_id "$USER_ID"; then
    BRANCH_GUESS="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if is_valid_id "$BRANCH_GUESS"; then
      USER_ID="$BRANCH_GUESS"
    fi
  fi
fi

if ! is_valid_id "$USER_ID"; then
  echo "Error: Could not determine teammate ID. Pass <aanjneya|daksh|jivi|tim> or run ./scripts/setup.sh <id>." >&2
  exit 1
fi

[[ $# -lt 1 ]] && usage
SUMMARY="$1"
shift

if [[ "${1:-}" == "--include-challenge" ]]; then
  INCLUDE_CHALLENGE=true
  shift
fi
[[ $# -gt 0 ]] && usage

if ! git rev-parse --verify origin/main >/dev/null 2>&1; then
  echo "Error: origin/main not found. Push your initial commit to main first." >&2
  exit 1
fi

PATHS=(".brain")
if $INCLUDE_CHALLENGE; then
  PATHS+=("challenge")
fi

# Collect local changes (modified, staged, deleted, untracked) under target paths
STATUS_OUTPUT="$(git -c core.quotepath=false status --porcelain=v1 -uall -- "${PATHS[@]}")"
if [[ -z "$STATUS_OUTPUT" ]]; then
  echo "No local changes in ${PATHS[*]} to push."
  exit 0
fi

# Enforce 25MB limit for pushed files
MAX_BYTES=$((25 * 1024 * 1024))
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  path="${line:3}"
  if [[ "$path" == *" -> "* ]]; then
    path="${path##* -> }"
  fi
  if [[ -f "$path" ]]; then
    size="$(wc -c < "$path" | tr -d ' ')"
    if (( size > MAX_BYTES )); then
      echo "Error: '$path' is >25MB ($size bytes). Keep large raw datasets out of Git." >&2
      exit 1
    fi
  fi
done <<< "$STATUS_OUTPUT"

echo "Fetching latest origin/main..."
git fetch --quiet origin main

WORKTREE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/brain-sync-XXXXXX")"
TMP_SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/brain-merge-XXXXXX")"
cleanup() {
  git worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1 || true
  rmdir "$WORKTREE_DIR" >/dev/null 2>&1 || true
  [[ -n "${TMP_SCRATCH:-}" && -d "$TMP_SCRATCH" ]] && find "$TMP_SCRATCH" -depth -delete >/dev/null 2>&1 || true
}
trap cleanup EXIT

git worktree add --quiet --detach "$WORKTREE_DIR" origin/main

is_binary_file() {
  local f="$1"
  [[ ! -s "$f" ]] && return 1
  local numstat
  numstat="$(git diff --no-index --numstat -- /dev/null "$f" 2>/dev/null || true)"
  [[ "$numstat" == -* ]]
}

apply_delete() {
  local p="$1"
  local head_blob remote_blob
  head_blob="$(git rev-parse "HEAD:$p" 2>/dev/null || true)"
  remote_blob="$(git rev-parse "origin/main:$p" 2>/dev/null || true)"

  if [[ -n "$remote_blob" && "$head_blob" != "$remote_blob" ]]; then
    echo "Conflict: '$p' was deleted locally, but modified on origin/main by a teammate." >&2
    echo "Run 'git fetch origin && git merge origin/main' to resolve, then re-run $0." >&2
    exit 1
  fi
  git -C "$WORKTREE_DIR" rm -f --ignore-unmatch -- "$p" >/dev/null 2>&1 || true
}

apply_upsert() {
  local p="$1"
  local head_blob remote_blob
  head_blob="$(git rev-parse "HEAD:$p" 2>/dev/null || true)"
  remote_blob="$(git rev-parse "origin/main:$p" 2>/dev/null || true)"

  # Case 1: New file locally (not in HEAD)
  if [[ -z "$head_blob" ]]; then
    if [[ -n "$remote_blob" ]]; then
      if [[ -f "$WORKTREE_DIR/$p" ]] && cmp -s "$p" "$WORKTREE_DIR/$p"; then
        return 0
      fi
      echo "Conflict: '$p' was created both locally and on origin/main with different contents." >&2
      echo "Run 'git fetch origin && git merge origin/main' to resolve, then re-run $0." >&2
      exit 1
    fi
    mkdir -p "$WORKTREE_DIR/$(dirname "$p")"
    cp "$p" "$WORKTREE_DIR/$p"
    git -C "$WORKTREE_DIR" add -- "$p"
    return 0
  fi

  # Case 2: Tracked file locally, deleted on origin/main
  if [[ -z "$remote_blob" ]]; then
    echo "Conflict: '$p' was modified locally, but deleted on origin/main by a teammate." >&2
    echo "Run 'git fetch origin && git merge origin/main' to resolve, then re-run $0." >&2
    exit 1
  fi

  # Case 3: origin/main did NOT change this file since HEAD — fast path
  if [[ "$head_blob" == "$remote_blob" ]]; then
    mkdir -p "$WORKTREE_DIR/$(dirname "$p")"
    cp "$p" "$WORKTREE_DIR/$p"
    git -C "$WORKTREE_DIR" add -- "$p"
    return 0
  fi

  # Case 4: Concurrent edit on both origin/main and local — true 3-way merge
  local base_file="$TMP_SCRATCH/base"
  local local_file="$TMP_SCRATCH/local"
  git show "HEAD:$p" > "$base_file"
  cp "$p" "$local_file"

  if is_binary_file "$local_file" || is_binary_file "$WORKTREE_DIR/$p"; then
    echo "Conflict: binary file '$p' changed both locally and on origin/main." >&2
    echo "Run 'git fetch origin && git merge origin/main' to resolve, then re-run $0." >&2
    exit 1
  fi

  if ! git merge-file --quiet "$WORKTREE_DIR/$p" "$base_file" "$local_file"; then
    echo "Conflict: overlapping edits in '$p' between local changes and origin/main." >&2
    echo "Run 'git fetch origin && git merge origin/main' to resolve manually, then re-run $0." >&2
    exit 1
  fi

  git -C "$WORKTREE_DIR" add -- "$p"
}

ALL_PUSHED=()
DELETED_PATHS=()

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  xy="${line:0:2}"
  raw_path="${line:3}"

  if [[ "$raw_path" == *" -> "* ]]; then
    old_path="${raw_path%% -> *}"
    new_path="${raw_path##* -> }"
    apply_delete "$old_path"
    apply_upsert "$new_path"
    ALL_PUSHED+=("$old_path" "$new_path")
    DELETED_PATHS+=("$old_path")
  elif [[ "$xy" == " D" || "$xy" == "D " || "$xy" == "DD" ]]; then
    apply_delete "$raw_path"
    ALL_PUSHED+=("$raw_path")
    DELETED_PATHS+=("$raw_path")
  else
    apply_upsert "$raw_path"
    ALL_PUSHED+=("$raw_path")
  fi
done <<< "$STATUS_OUTPUT"

if git -C "$WORKTREE_DIR" diff --cached --quiet; then
  echo "Changes already match origin/main — nothing to commit."
  exit 0
fi

COMMIT_MSG="brain(${USER_ID}): ${SUMMARY}"
git -C "$WORKTREE_DIR" commit --quiet -m "$COMMIT_MSG"

# Push with automatic rebase retry (handles simultaneous teammate pushes)
PUSHED=false
for attempt in 1 2 3; do
  if git -C "$WORKTREE_DIR" push --quiet origin HEAD:main; then
    PUSHED=true
    break
  fi
  echo "Push race detected (attempt $attempt/3) — rebasing onto latest origin/main..."
  git -C "$WORKTREE_DIR" fetch --quiet origin main
  if ! git -C "$WORKTREE_DIR" rebase --quiet origin/main; then
    git -C "$WORKTREE_DIR" rebase --abort >/dev/null 2>&1 || true
    echo "Error: Conflict while rebasing ${PATHS[*]} onto origin/main." >&2
    echo "Run 'git fetch origin && git merge origin/main' to resolve manually, then re-run $0." >&2
    exit 1
  fi
done

if ! $PUSHED; then
  echo "Error: Failed to push to origin/main after 3 attempts." >&2
  exit 1
fi

echo "Pushed '$COMMIT_MSG' to origin/main."

# Snapshot final pushed state before syncing local workspace (guarantees zero local loss)
SNAPSHOT_DIR="$TMP_SCRATCH/pushed"
mkdir -p "$SNAPSHOT_DIR"
if (( ${#ALL_PUSHED[@]} > 0 )); then
  for p in "${ALL_PUSHED[@]}"; do
    if [[ -f "$WORKTREE_DIR/$p" ]]; then
      mkdir -p "$SNAPSHOT_DIR/$(dirname "$p")"
      cp "$WORKTREE_DIR/$p" "$SNAPSHOT_DIR/$p"
    fi
  done
fi

# Sync local workspace cleanly without touching non-brain WIP files
git fetch --quiet origin main

if (( ${#ALL_PUSHED[@]} > 0 )); then
  for p in "${ALL_PUSHED[@]}"; do
    git reset --quiet HEAD -- "$p" >/dev/null 2>&1 || true
    if git cat-file -e "HEAD:$p" 2>/dev/null; then
      git checkout --quiet HEAD -- "$p" >/dev/null 2>&1 || true
    elif [[ -f "$p" ]] && git cat-file -e "origin/main:$p" 2>/dev/null; then
      rm -- "$p"
    fi
  done
fi

MERGED=false
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" == "main" ]]; then
  if git merge --quiet --ff-only origin/main 2>/dev/null; then
    MERGED=true
  fi
else
  if git merge --quiet origin/main --no-edit 2>/dev/null; then
    MERGED=true
  else
    git merge --abort >/dev/null 2>&1 || true
  fi
fi

# If local merge couldn't run (e.g., dirty non-brain WIP), restore exact pushed
# brain state into working tree so local files stay up-to-date and nothing is lost.
if ! $MERGED; then
  if (( ${#ALL_PUSHED[@]} > 0 )); then
    for p in "${ALL_PUSHED[@]}"; do
      if [[ -f "$SNAPSHOT_DIR/$p" ]]; then
        mkdir -p "$(dirname "$p")"
        cp "$SNAPSHOT_DIR/$p" "$p"
      fi
    done
  fi
  if (( ${#DELETED_PATHS[@]} > 0 )); then
    for d in "${DELETED_PATHS[@]}"; do
      if [[ ! -f "$SNAPSHOT_DIR/$d" && -f "$d" ]]; then
        rm -- "$d"
      fi
    done
  fi
  echo "Note: origin/main updated and local .brain/ kept in sync; run 'git merge origin/main --no-edit' when your WIP is committed."
else
  echo "Second brain synced with origin/main."
fi
