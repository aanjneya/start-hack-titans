#!/usr/bin/env bash
# Stack-agnostic fast verification before pushing feature code or merging to main.
# Auto-detects Node/TS and Python projects once scaffolded, and enforces env/secret/size hygiene.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

FAIL=0

echo "==> Running hackathon verification..."

# 1. Secret hygiene: ensure no secret .env files are tracked or staged (root or subdirs)
if git ls-files | grep -E '(^|/)\.env($|\..+)' | grep -v '\.env\.example$' >/dev/null 2>&1; then
  echo "ERROR: A secret .env file is tracked in Git! Remove it with: git rm --cached <file>" >&2
  FAIL=1
fi
if git diff --cached --name-only | grep -E '(^|/)\.env($|\..+)' | grep -v '\.env\.example$' >/dev/null 2>&1; then
  echo "ERROR: A secret .env file is staged for commit!" >&2
  FAIL=1
fi

# 2. .env.example parity check (warn only so teammates catch missing keys fast)
if [[ -f ".env.example" ]]; then
  if [[ -f ".env" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" || "$line" == \#* ]] && continue
      key="${line%%=*}"
      [[ -z "$key" ]] && continue
      if ! grep -q "^${key}=" .env; then
        echo "WARN: Key '${key}' from .env.example is missing in local .env (check pinned team .env)." >&2
      fi
    done < .env.example
  else
    echo "WARN: Local .env not found. Copy .env.example -> .env or run ./scripts/setup.sh <id>." >&2
  fi
fi

# 3. 25MB tracked file guard
MAX_BYTES=$((25 * 1024 * 1024))
while IFS= read -r f; do
  [[ -z "$f" || ! -f "$f" ]] && continue
  size="$(wc -c < "$f" | tr -d ' ')"
  if (( size > MAX_BYTES )); then
    echo "ERROR: Tracked file '$f' exceeds 25MB ($size bytes)." >&2
    FAIL=1
  fi
done < <(git ls-files)

# 4. Auto-detect Node / TypeScript packages (root + common hackathon subdirs)
run_node_checks() {
  local dir="$1"
  [[ ! -f "$dir/package.json" ]] && return 0

  if [[ ! -d "$dir/node_modules" && ! -d "$REPO_ROOT/node_modules" ]]; then
    echo "ERROR: '$dir/package.json' exists but node_modules is missing. Install dependencies first." >&2
    FAIL=1
    return 0
  fi

  local pm="npm"
  if [[ -f "$dir/pnpm-lock.yaml" || -f "$REPO_ROOT/pnpm-lock.yaml" ]]; then
    pm="pnpm"
  elif [[ -f "$dir/yarn.lock" || -f "$REPO_ROOT/yarn.lock" ]]; then
    pm="yarn"
  elif [[ -f "$dir/bun.lockb" || -f "$dir/bun.lock" || -f "$REPO_ROOT/bun.lockb" || -f "$REPO_ROOT/bun.lock" ]]; then
    pm="bun"
  fi

  has_script() {
    node -e "const p=require('./package.json'); process.exit(p.scripts && p.scripts[process.argv[1]] ? 0 : 1)" "$1" 2>/dev/null
  }

  echo "==> Checking Node/TS package in '$dir' (via $pm)..."
  (
    cd "$dir"
    if has_script verify; then
      "$pm" run verify
    else
      if has_script typecheck; then "$pm" run typecheck; fi
      if has_script lint; then "$pm" run lint; fi
      if has_script build; then "$pm" run build; fi
    fi
  ) || FAIL=1
}

for candidate in "." "frontend" "web" "app" "client" "backend" "server" "api"; do
  if [[ -f "$candidate/package.json" ]]; then
    run_node_checks "$candidate"
  fi
done

# 5. Auto-detect Python code
if command -v python3 >/dev/null 2>&1; then
  PY_FILES="$(find . -maxdepth 4 -name '*.py' -not -path '*/.venv/*' -not -path '*/venv/*' -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/__pycache__/*' | head -n 1 || true)"
  if [[ -n "$PY_FILES" ]]; then
    echo "==> Checking Python syntax..."
    python3 -m compileall -q -x '(\.venv|venv|node_modules|\.git|__pycache__)' . || FAIL=1
  fi
fi

if (( FAIL != 0 )); then
  echo "Verification FAILED." >&2
  exit 1
fi

echo "Verification PASSED."
