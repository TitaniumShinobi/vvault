# VVAULT Local Launcher Rubric

This rubric defines the pass/fail standard for the operator-facing `vvault` command.

## Purpose

`vvault` is an operator command, not a raw dev command.

Its job is to:

1. reuse or start the local app
2. open the browser
3. print the short success line
4. avoid making raw dev logs the primary terminal UX

## Required behavior

### Shell binding

- `vvault` must resolve to a shell function from [`~/.zshrc`](/Users/devonwoodson/.zshrc)
- that function must invoke [`scripts/open-vvault-standalone.sh`](../../scripts/open-vvault-standalone.sh) with `bash`
- `vvault` must not resolve to `repo_up "vvault"`

### Launcher behavior

- if `7784` is already serving VVAULT, reuse it
- if VVAULT is not already live, start the existing full-stack repo path
- wait for the frontend on `7784` and backend process health on `8000`
- report strict `/api/ready` state separately from shallow `/api/health`
- open the default browser to `http://localhost:7784`
- print `VVAULT is running at http://localhost:7784`

### Raw dev separation

- `npm run dev`
- `npm run dev:full`

These remain the terminal-first debugging paths.

They are not the reference UX for `vvault`.

## Failure signatures

Any of the following is a rubric violation:

- `vvault` prints `> vvault-frontend@1.0.0 dev:full` as the primary terminal UX
- `vvault` never opens the browser
- `vvault` shows raw webpack or backend logs instead of a short success line
- `type vvault` reports an alias instead of a shell function
- ambiguous duplicate backend listeners exist on `8000`
- degraded `/api/ready` is described as canonical readiness
- the shell shows `BASH_SOURCE[0]: parameter not set`

## Acceptance checklist

- `type vvault` reports a shell function
- `vvault` from a fresh shell opens `http://localhost:7784`
- a second `vvault` run reuses the existing app
- `vvault` prints the success line
- `/api/ready` is checked as the canonical readiness gate
- degraded `/api/ready` remains visible instead of being called healthy
- `npm run dev` still behaves as raw frontend startup
- `npm run dev:full` still behaves as raw full-stack startup
