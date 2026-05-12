<div align="center">
  <img src="./assets/vvault_glyph.png" alt="VVAULT Logo">
</div>

# VVAULT

Canonical cross-product user registry spec: `docs/CANONICAL_CROSS_PRODUCT_USER_REGISTRY_SPEC.md`

**Voice & Vaulted Autonomy for Unfragmented Long-Term Tethering**: the continuity, voice, autonomy, and tethering layer.
**Verified Vectored Anatomy Unconsciously Lingering Together**: the product-domain model for protected anatomy bodies.
**Official Verified Vectored Anatomy Unconsciously Lingering Together Stance**
VVAULT = local body database + VVAULT-native auth/session + VVAULT-native file/storage ownership + provenance-aware integrations
- Vectored Anatomies (repositories)
- Personal Files
- Entire Databases

## Overview

VVAULT is an advanced vault/drive for **Vectored Anatomies**.

A **Vectored Anatomy** is a protected, identity-bearing directory body. It can represent a human, AI, product, project, repository, place, object, organization, service, or system.

VVAULT combines directory storage, capsule and glyph identity, security history, witness history, permissions, integrations, and recovery/rematerialization support. Existing construct memory features are one implementation slice of the broader Vectored Anatomy model, not the definition of the product.

The canonical architecture note lives in `docs/architecture/VECTORED_ANATOMY_MODEL.md`.

## Local web startup

Canonical local web URL: `http://localhost:7784`

Operator-facing launcher:

```bash
./bin/vvault
```

`./bin/vvault` calls `scripts/open-vvault-standalone.sh`. It mirrors the `code` launcher behavior: if VVAULT is already live on `7784` it reuses the running app, otherwise it starts the repo's existing full-stack dev command in the background with `npm run dev:full`, waits for the frontend and backend to become healthy, opens the default browser, and prints `VVAULT is running at http://localhost:7784`.

The canonical launcher contract lives in `docs/VVAULT_STARTUP_CONTRACT.md`.

Runtime closure is stricter than process health. Use `docs/operations/RUNTIME_CLOSURE_CHECKLIST.md` to prove `/api/ready`, local body database health, VVAULT-native auth/session readiness, file/storage ownership, and write blocking before calling VVAULT ready.

If `vvault` still prints raw `npm run dev:full` logs instead of opening the browser, your current shell is probably still using the old repo alias. Run `source ~/.zshrc` or open a fresh terminal window, then try again.

If you want a shell command from any directory, either symlink it into your `PATH`:

```bash
ln -sf "$(pwd)/bin/vvault" /usr/local/bin/vvault
```

or add a shell function in `~/.zshrc`:

```bash
vvault() {
  /Users/devonwoodson/Library/Mobile\ Documents/com~apple~CloudDocs/Documents/GitHub/vvault/bin/vvault "$@"
}
```

Raw repo dev paths stay terminal-first:

- `npm run dev` starts the raw frontend-only webpack dev server on `7784`.
- `npm run dev:full` starts the raw frontend + backend stack and keeps the live logs in your terminal.

Use `./bin/vvault` when you want the operator flow. Use the raw `npm` commands when you want direct dev-server output or startup debugging.

Quick verification:

1. Run `./bin/vvault` from a fresh shell. It should start VVAULT and open `http://localhost:7784`.
2. Run `./bin/vvault` again. It should reuse the existing local server and print the same success line without dumping the raw dev logs as the primary UX.
3. Run `npm run dev` or `npm run dev:full` directly when you want the raw terminal-first path.

## Current Anatomy Body Shape

```
VVAULT/
├── src/                       # React vault/drive interface
├── vvault/
│   ├── server/                # Flask API and integration surfaces
│   ├── audit/                 # Security and witness history helpers
│   ├── security/              # Guards, sentinels, recovery-related code
│   ├── memory/                # Capsule and memory infrastructure
│   ├── boot/                  # Pocketverse boot and continuity support
│   ├── layers/                # Higher-plane / witness manifests
│   └── data/                  # Runtime ledgers and local state
├── frame/                     # Body/neural/memory anatomy components
├── docs/                      # Architecture, operations, legal, and rubrics
├── public/                    # Frontend shell
└── assets/                    # VVAULT glyph and interface assets
```

## Historical Nova Import

**Date**: August 3, 2025  
**Source**: `Nova (macos)/Vault/`  
**Destination**: `VVAULT (macos)/nova_profile/`  
**Files Migrated**: 3,405 files  
**Integrity**: ✅ Complete - All files preserved with absolute integrity

### Migration Details

- **Source Structure**: Original `Vault/` directory contained all Nova's memory data
- **Destination**: New modular `VVAULT/nova_profile/` structure
- **File Count**: 3,405 files successfully migrated (plus 1 new `__init__.py`)
- **Permissions**: All file permissions and timestamps preserved
- **References Updated**: Updated path references in Nova Terminal modules

This import is historical context for an early AI/VSI anatomy body. It does not limit VVAULT to Nova, AI constructs, or memory-only storage.

## Current Feature Mapping

- **Vault browser and `vault_files` storage**: directory body storage for structured anatomy artifacts.
- **Construct instances**: the current AI/VSI anatomy subtype, keyed by compatibility fields such as `construct_id` and callsign.
- **Capsules**: anatomy snapshots for memory, identity, state, and recovery material.
- **Glyphs**: identity marks attached to protected anatomy bodies.
- **Identity projection**: explicit identity vector persistence for fields such as conditioning, definition, physical features, voice, prompt, and avatar.
- **Audit/compliance logs**: security and witness history.
- **Pocketverse guard, continuity ledger, and rematerialization seed**: witness, authority, recovery, and rematerialization support.
- **Chatty integration**: provider/runtime ingestion for transcripts, identity updates, memory material, and subject context.
- **Frame directory**: body, neural, and memory anatomy components.
- **Service config and credential storage**: service/system anatomy support.

## Compatibility Rules

- Do not rename existing routes, folders, legacy provenance columns, `construct_id`, `instances`, `capsules`, or `vault_files` as part of product-language alignment.
- Treat `construct_id` as the current compatibility subject key for AI/VSI anatomies.
- Future neutral anatomy metadata, such as `anatomy_type` and `anatomy_id`, should be layered in without breaking current clients.
- Construct and capsule language remains valid for feature/subtype descriptions, but not as the whole-product definition.

## Future Development

### Planned VVAULT Features

1. **Core Logic** (`core/`)
   - Memory indexer
   - Vector database logic
   - Semantic tagging system

2. **Voice Logs** (`voice_logs/`)
   - Raw/processed audio transcripts
   - Discord and agent mic feed processing

3. **Snapshots** (`snapshots/`)
   - Point-in-time memory states
   - Emotional event linking

4. **Tags** (`tags/`)
   - JSON/YAML-based label sets
   - Emotion/state classification

5. **Archive** (`archive/`)
   - Cold storage for long-term immutables
   - Encrypted .zip/.jsonl bundles

6. **Keys** (`keys/`)
   - API keys and fingerprint hashes
   - Vault decrypt credentials

## Modular Roles

| Component | Role in Vectored Anatomy |
|-----------|--------------------------|
| `snapshots/` | Rollback and reconstruction of identity-bearing events |
| `voice_logs/` | Voice vector history and contradiction tracing |
| `tags/` | Anatomy artifact categorization |
| `archive/` | Cold storage for long-term protected records |
| `glyphs/` | Visual identity and authenticity marks |
| `audit/` | Security and witness history |
| `recovery/` | Recovery and rematerialization support |

## Security & Integrity

- **File Integrity**: All 3,405 files migrated with absolute integrity
- **Path References**: Updated all Nova codebase references
- **Modular Design**: Isolated memory system for enhanced security
- **Backup Preservation**: All backup files and timestamps maintained

## Usage

Use the local web app or service APIs to work with anatomy directory bodies, current AI/VSI construct anatomies, capsules, glyphs, witness history, and integrations.

---

**Note**: VVAULT preserves its original voice/autonomy/tethering identity while making Verified Vectored Anatomy the central product model.
