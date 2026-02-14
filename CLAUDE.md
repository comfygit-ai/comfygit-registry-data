# Claude Instructions

<!-- Keep this file in sync with AGENTS.md. -->

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Community Mappings Maintenance

When adding fallback mappings for common unresolved nodes:

1. Edit `config/community_mappings.json`.
2. Add entries under `mappings` with this schema:
   - `node_type` (e.g. `SetNode`)
   - `input_signature` (usually `_`)
   - `package_id` (must exist in `packages` table)
   - `reason`
   - `source_url`
   - `added_at` (`YYYY-MM-DD`)
3. Keep mappings fallback-only. Do not use this file to override existing registry/manager keys.
4. Validate:
   ```bash
   uv run pytest tests/integration/test_community_mappings_fallback.py -v
   uv run pytest tests/integration -q
   ```
5. Optional local simulation:
   - Run `src/augment_mappings.py` against a copy of `data/node_mappings.json` with `--community config/community_mappings.json`.
   - Confirm expected keys are added and no existing keys are overwritten.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
