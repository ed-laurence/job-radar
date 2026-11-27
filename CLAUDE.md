@AGENTS.md

## Claude Code specifics
- Subagents live in `.claude/agents/`. Their definitions contain only `Role`, `Scope`, `Process`, `Output` and `References`. Standards stay in `AGENTS.md` and `docs/standards/`, never in agent files.
- Human-only agents (`cloud-architect`, `devops-engineer`, `postgres-pro`, `coordinator-developer`, `ui-ux-tester`, `performance-engineer`, `privacy-reviewer`) help build the harness and infra. The dev board never dispatches them.
- When orchestrating parallel subagents, give each one exclusive paths (a lease). Shared hotspots have a single writer: `api/openapi.yaml`, `*/generated/**`, `backend/pyproject.toml`, `web/package.json`, lockfiles, `backend/migrations/`.
- Report outcomes faithfully. If a check was skipped because a tool is missing, say which one.
