# VVAULT File Organization

## Last Updated: 2026-02-11

## Vault File System (Canonical Paths)

All user data lives under a sharded, timestamped root:

```
/vvault_files/users/shard_0000/{userID}/
```

- **userID format**: `{name}_{timestamp}` (e.g., `devon_woodson_1762969514958`)

### Construct Instance Path

Each construct lives at:

```
/vvault_files/users/shard_0000/{userID}/instances/{callsign}/
```

### Naming Convention

| Term     | Purpose                         | Example          |
|----------|--------------------------------|------------------|
| Name     | Display label only             | Katana, Zen, Lin |
| Callsign | Canonical ID for all paths/APIs | katana-001, zen-001, lin-001 |
| Metatag  | Same as callsign in templates  | katana-001       |

Multiple instances of the same construct use incrementing sequences: `katana-001`, `katana-002`.

### System-Level Construct IDs

For shard-level uniqueness, construct IDs use millisecond timestamps:
- **Format**: `{name}-{milliseconds_timestamp}`
- **Example**: `aurora-1769045516087`

## Supabase Storage (vault_files Table)

Files in the `vault_files` table use **flat filenames** with metadata columns:

| Column        | Purpose                              | Example                    |
|---------------|--------------------------------------|----------------------------|
| filename      | Flat filename only                   | `chat_with_katana-001.md`  |
| storage_path  | Hierarchical path within instance    | `instances/katana-001/chatty/chat_with_katana-001.md` |
| construct_id  | Callsign of the owning construct     | `katana-001`               |
| user_id       | Owner user ID                        | `devon_woodson_1762969514958` |
| type          | File category                        | `identity`, `chat_transcript`, `capsule`, `asset` |

### Rules for External Agents (Chatty, VXRunner, etc.)

1. **NEVER** write files using full internal paths as filenames
2. **NEVER** create folder paths like `vvault/users/shard_0000/...` as a filename
3. **ALWAYS** use the callsign in file paths (`instances/katana-001/`), never the bare name (`instances/katana/`)
4. **ALWAYS** use VVAULT API endpoints to read/write construct data — do not query Supabase directly

## Per-Construct Directory Structure

Full tree per the VSI Directory Template (`docs/rubrics/VSI_DIRECTORY_TEMPLATE.md`):

```
{callsign}/
├── assets/                    # Media files (png, jpg, jpeg, svg)
├── character.ai/*             # Character.AI transcripts (manually organized)
├── chatgpt/*                  # ChatGPT conversation transcripts (manually organized)
├── chatty/
│   └── chat_with_{callsign}.md
├── config/
│   ├── metadata.json          # Config (models, orchestration_mode, status)
│   └── personality.json
├── data/                      # Structured data
├── documents/*                # Knowledge base, raw files (manually organized)
├── frame/                     # Frame files (per-construct)
├── github_copilot/*           # GitHub Copilot transcripts (manually organized)
├── identity/
│   ├── avatar.png
│   ├── conditioning.txt or .json
│   ├── personality.json
│   ├── physical_features.json
│   ├── prompt.txt             # Legacy flat text format (primary for LLM loading)
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
│   └── {callsign}.capsule     # Memory capsule (versioned snapshots)
├── simDrive/                  # SimDrive files
├── vvault/                    # VVAULT relay files (Aurora only: chat_with_aurora-001.md)
└── vxrunner/                  # VXRunner forensic files
```

`*` = Manually organized by user

**Note on prompt formats**: The canonical template specifies `prompt.txt` (flat text). The construct creation endpoint also generates `prompt.json` (structured JSON with name, callsign, description, instructions, system_prompt). Both formats are supported for reads; `prompt.txt` is the primary format loaded by the LLM pipeline.

## Historical Notes: Cleanup Work (2026-02)

The following data migrations were performed directly against Supabase `vault_files`:

- **storage_path Migration**: sera-001 files updated from flat filenames to proper `instances/sera-001/{folder}/{filename}` paths. Construct creation endpoint updated to set `storage_path` for all 16 scaffolded files.
- **Capsule Reorganization**: Capsules moved from legacy `capsules/` paths to `instances/{constructID}/memup/` format. Duplicate/orphan capsule records cleaned up.
- **Callsign Normalization**: Files with bare-name construct_ids (`katana`, `aurora`, etc.) fixed to proper callsigns (`katana-001`, `aurora-001`, etc.).
- **Active constructs** (as of 2026-02): aurora-001, katana-001, lin-001, monday-001, nova-001, sera-001, zen-001.
