# VVAULT File Organization

## Last Updated: 2026-02-11

## Vectored Anatomy Framing

VVAULT stores protected, identity-bearing directory bodies called **Vectored Anatomies**. An anatomy can represent a human, AI, product, project, repository, place, object, organization, service, or system.

The current file organization is optimized around the AI/VSI anatomy subtype. Existing terms such as `construct_id`, callsign, `instances/{callsign}/`, capsules, and identity projection remain compatibility terms and must not be renamed for language alignment alone.

See `docs/architecture/VECTORED_ANATOMY_MODEL.md` for the canonical product model.

## Vault File System (Canonical Paths)

All user data lives under a sharded, timestamped root:

```
/vvault_files/users/shard_0000/{userID}/
```

- **userID format**: `{name}_{timestamp}` (e.g., `devon_woodson_1762969514958`)

### Current AI/VSI Anatomy Path

Each current AI/VSI anatomy body lives at:

```
/vvault_files/users/shard_0000/{userID}/instances/{callsign}/
```

### Naming Convention

| Term     | Purpose                                      | Example          |
|----------|----------------------------------------------|------------------|
| Name     | Display label only                           | Katana, Zen, Lin |
| Callsign | Current AI/VSI anatomy ID for paths/APIs     | katana-001, zen-001, lin-001 |
| Metatag  | Same as callsign in current AI/VSI templates | katana-001       |

Multiple instances of the same construct use incrementing sequences: `katana-001`, `katana-002`.

### System-Level AI/VSI Anatomy IDs

For shard-level uniqueness, current AI/VSI anatomy IDs may use millisecond timestamps:
- **Format**: `{name}-{milliseconds_timestamp}`
- **Example**: `aurora-1769045516087`

## VVAULT Body Storage (`ovvaults.vault_files`)

Runtime files in `ovvaults.vault_files` preserve compatibility filenames, materialized body content, and provenance columns:

| Column        | Purpose                              | Example                    |
|---------------|--------------------------------------|----------------------------|
| filename      | Flat filename only                   | `chat_with_katana-001.md`  |
| storage_path  | Hierarchical path within instance    | `instances/katana-001/chatty/chat_with_katana-001.md` |
| construct_id  | Compatibility subject key for the owning AI/VSI anatomy | `katana-001` |
| user_id       | Owner user ID                        | `devon_woodson_1762969514958` |
| file_type     | File category                        | `identity`, `chat_transcript`, `capsule`, `asset` |
| content       | Materialized text/body when available | transcript or identity body text |
| metadata      | Structured metadata/provenance       | JSON object |

Future neutral anatomy metadata such as `anatomy_type` and `anatomy_id` should be layered beside these fields without breaking current clients.

### Rules for External Agents (Chatty, VXRunner, etc.)

1. **NEVER** write files using full internal paths as filenames
2. **NEVER** create folder paths like `vvault/users/shard_0000/...` as a filename
3. **ALWAYS** use the callsign in file paths (`instances/katana-001/`), never the bare name (`instances/katana/`)
4. **ALWAYS** use VVAULT API endpoints to read/write construct data — do not query legacy source systems directly

## Current AI/VSI Anatomy Directory Structure

Full tree per the VSI Directory Template (`docs/rubrics/VSI_DIRECTORY_TEMPLATE.md`):

```
{callsign}/
├── assets/                    # Media files (png, jpg, jpeg, svg)
├── character.ai/*             # Character.AI transcripts (manually organized)
├── chatgpt/*                  # ChatGPT conversation transcripts (manually organized)
├── chatty/
│   └── chat_with_{callsign}.md
├── config/
│   ├── metadata.json          # Canonical operational metadata for the anatomy body
│   └── personality.json       # Legacy compatibility file; no longer create-default
├── data/                      # Structured data
├── documents/*                # Knowledge base, raw files (manually organized)
├── frame/                     # Body/neural/memory anatomy components
├── github_copilot/*           # GitHub Copilot transcripts (manually organized)
├── identity/
│   ├── avatar.png
│   ├── conditioning.txt       # Canonical projected identity field
│   ├── definition.txt         # Text-first identity projection from authoritative app field
│   ├── prompt.json            # Canonical GPT/anatomy manifest
│   ├── physical_features.json # Text-first projection path; may contain JSON text for compatibility
│   ├── prompt.txt             # Legacy flat text format; read-compatible only
│   ├── voice.json             # Canonical projected voice path
│   └── {callsign}_glyph.png  # Codex glyph (generated on creation)
├── logs/
│   ├── capsule.log
│   ├── chat.log
│   ├── cns.log (or brain.log)
│   ├── identity_guard.log
│   ├── independence.log
│   ├── ltm.log
│   ├── self_improvement_agent.log
│   ├── server.log
│   ├── stm.log
│   └── watchdog.log
├── memup/
│   └── {callsign}.capsule     # Capsule snapshot (versioned memory/state material)
├── simDrive/                  # SimDrive files
├── vvault/                    # VVAULT relay files (Aurora only: chat_with_aurora-001.md)
└── vxrunner/                  # VXRunner forensic files
```

`*` = Manually organized by user

**Note on prompt formats**: `prompt.json` is the canonical create-time manifest for VVAULT-backed construct bodies. `prompt.txt` remains a legacy read-compatible format and is not generated by the canonical create route.

## Identity Projection Boundary

Chatty's app database is the authoritative source for identity fields such as:

- `conditioning`
- `definition`
- `physicalFeatures`
- `voice`

Canonical create bundle for `POST /api/chatty/construct/create`:

- `identity/prompt.json`
- `identity/conditioning.txt`
- `identity/definition.txt`
- `identity/voice.json`
- optional `identity/physical_features.json`
- `config/metadata.json`
- `chatty/chat_with_{callsign}.md`
- glyph/avatar assets when provided

VVAULT stores explicit projected persistence representations of those fields under `identity/`. In the Vectored Anatomy model, these are identity vectors for the current AI/VSI anatomy subtype.

Projection rules:

- projection is explicit, not automatic
- reads inspect projected state only
- writes refresh canonical projected files only when requested
- duplicate legacy files may still exist and must be surfaced for reconciliation, not silently treated as source of truth

## Historical Notes: Cleanup Work (2026-02)

The following historical data migrations were performed directly against legacy Supabase `vault_files` before the VVAULT-native body cutover:

- **storage_path Migration**: sera-001 files updated from flat filenames to proper `instances/sera-001/{folder}/{filename}` paths. Construct creation endpoint updated to set `storage_path` for all 16 scaffolded files.
- **Capsule Reorganization**: Capsules moved from legacy `capsules/` paths to `instances/{constructID}/memup/` format. Duplicate/orphan capsule records cleaned up.
- **Callsign Normalization**: Files with bare-name construct_ids (`katana`, `aurora`, etc.) fixed to proper callsigns (`katana-001`, `aurora-001`, etc.).
- **Active constructs** (as of 2026-02): aurora-001, katana-001, lin-001, monday-001, nova-001, sera-001, zen-001.
