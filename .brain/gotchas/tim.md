# Gotchas (tim)

- **brain-push.sh false "overlapping edits" conflict on Windows** (2026-11-21): with `core.autocrlf=true` the temp worktree checks files out as CRLF while the merge base (`git show HEAD:`) is LF, so `git merge-file` sees every line changed. Workaround: convert the edited file to LF (`sed -i "s/\r$//" <file>`) and run `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.autocrlf GIT_CONFIG_VALUE_0=false ./scripts/brain-push.sh "<msg>"`. Real fix: normalize EOL in the script or add `.gitattributes` with `*.md text eol=lf`.
