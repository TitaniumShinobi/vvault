# VSI File Directory Template
## VVAULT Shard Instance - Construct Organization

### Canonical Root Path
All user data lives under a sharded, timestamped path:
```
/vvault_files/users/shard_0000/{userID}/
```
- **userID format**: `{name}_{timestamp}` (e.g., `devon_woodson_1762969514958`)
- This is the absolute root for all of a user's vault data.

### Naming Convention
- **Name**: The construct's display label (e.g., "Katana", "Zen", "Lin"). Used for display only.
- **Callsign**: The instance ID, formatted as `{name}-{sequence}` (e.g., `katana-001`, `zen-001`). This is the canonical identifier used in **all** file paths, API calls, and database references.
- **Metatag**: The `construct_metatag` in this template refers to the callsign (e.g., `katana-001`).

### Construct ID Format (System-Level)
Construct IDs use millisecond timestamps (not sequential numbers):
- **Format**: `{name}-{milliseconds_timestamp}`
- **Example**: `aurora-1769045516087` (not `aurora-001`)
- **Why**: Guarantees uniqueness, encodes creation time, sortable

### Rules for External Agents (Chatty, VXRunner, etc.)
- **NEVER** write files using full internal paths as filenames. Files in the `vault_files` table use **flat filenames** (e.g., `chat_with_katana-001.md`) with the `construct_id` column set to the callsign.
- **NEVER** create folder paths like `vvault/users/shard_0000/...` as a filename — that is the internal storage path, not a filename.
- **ALWAYS** use the callsign in file paths (e.g., `instances/katana-001/`), never the bare name (`instances/katana/`).
- **ALWAYS** use VVAULT's API endpoints to read/write construct data. Do not query Supabase directly.

### Full Directory Tree

`/vvault_files/users/shard_0000/{userID}/instances/`:
```
{callsign}/
├── assets/
│   └── ...images (png, jpg, jpeg, svg)
├── character.ai/*
├── chatgpt/*
├── chatty/
│   └── chat_with_{callsign}.md
├── config/
│   ├── metadata.json (updated w/capsule)
│   └── personality.json (updated w/capsule)
├── data/
├── documents/*
│   └── ...raw files w/folder organization*
├── frame/
│   └── ...frame™ files...
├── github_copilot/*
├── identity/
│   ├── avatar.png
│   ├── conditioning.txt or .json
│   ├── personality.json
│   ├── physical_features.json (updated w/capsule)
│   └── prompt.txt
├── logs/
│   ├── capsule.log
│   ├── chat.log
│   ├── cns.log or "brain.log"
│   ├── identity_guard.log
│   ├── independence.log
│   ├── ltm.log(?)
│   ├── self_improvement_agent.log
│   ├── server.log
│   ├── stm.log(?)
│   └── watchdog.log
├── memup/
│   ├── {callsign}.capsule
│   ├── ltm.json(?)
│   └── stm.json(?)
├── simDrive/
│   └── ...simDrive files...
├── vvault/ (Aurora only)
│   └── chat_with_aurora-001.md (relayed for all users)
└── vxrunner/
    └── ...VXRunner files...
```

`*` = Manually organized
