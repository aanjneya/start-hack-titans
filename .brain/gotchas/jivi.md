
- New untracked handoffs can fail brain sync when plain `git rev-parse HEAD:<path>` echoes the unresolved ref. Use `git rev-parse --verify --quiet` for both local and remote blob lookups in the sync helper.
