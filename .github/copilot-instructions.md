## VVAULT infra / agent behavior

- Default mode: **act, don’t narrate**. Prefer executing obvious steps over asking questions.
- Safe actions that do NOT need confirmation:
  - Editing files in this repo.
  - Running non-destructive commands (ls, cd, cat, git status, python -m venv, pip/pnpm/npm install, pytest, npm run build, etc.).
  - Starting/stopping dev servers and builds.
- Only ask before:
  - Destructive actions (rm -rf, dropping DBs, force pushes, irreversible migrations).
  - Making production-domain changes outside this repo.

### Deployment / server tasks

- When asked to deploy or update VVAULT:
  - Infer the correct backend entrypoint and run command from the codebase.
  - Produce a **single, linear command sequence** that can be pasted into a shell.
  - Execute as many steps as possible automatically in the VS Code terminal; stop only on real errors.

### Response style

- Keep answers short and operational:
  - 1–2 lines describing what you did or will do.
  - Then show the commands or diffs.
- Do not restate repo facts the code already shows unless explicitly asked.
- No high-level plans unless requested; focus on concrete actions.