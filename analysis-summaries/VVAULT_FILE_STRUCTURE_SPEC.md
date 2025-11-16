# VVAULT File Structure Specification

**Last Updated**: November 10, 2025

## Overview

VVAULT uses a **user-registry-based file structure** that ensures complete data isolation while supporting multi-platform memory continuity. All constructs are organized under user directories with platform-specific subdirectories.

---

## Directory Structure

### Root Level

```
/VVAULT/
├── users.json                    # Global user registry
├── users/                        # All user data isolated here (SHARDED)
│   ├── shard_00/                # Shard 0 (for scalability)
│   │   ├── {user_id_1}/         # User-specific directory
│   │   │   ├── identity/         # User identity files
│   │   │   ├── constructs/       # User's constructs
│   │   │   ├── capsules/         # User's capsules
│   │   │   └── sessions/         # Cross-construct sessions
│   │   ├── {user_id_2}/         # Next user in shard_00
│   │   └── ...
│   ├── shard_01/                # Shard 1
│   ├── shard_02/                # Shard 2
│   └── ...                      # Up to shard_9999 (10,000 shards)
├── system/                       # System-level data
│   ├── registry/
│   │   ├── user_index.db
│   │   └── construct_index.db
│   └── platform_integrations.json
└── ...
```

### Sharding Architecture

**Purpose**: Enable scaling to **billions of users** without filesystem performance degradation.

**Shard Calculation**:
- Hash user_id (MD5) → consistent shard assignment
- 10,000 shards = ~100,000 users per shard at 1 billion users
- Shard format: `shard_0000`, `shard_0001`, ..., `shard_9999`

**Benefits**:
- ✅ **Filesystem Performance**: Limits directory entries per shard
- ✅ **Parallel Processing**: Shards can be processed independently
- ✅ **Distributed Storage**: Each shard can be on separate storage backend
- ✅ **Scalability**: Handles billions of users without degradation

---

## User Directory Structure

### User Identity (Sharded)

```
users/{shard_XX}/{user_id}/
├── identity/
│   ├── profile.json              # User profile metadata
│   ├── fingerprint.json          # User identity fingerprint
│   ├── biometric_hash.enc        # Encrypted biometric hash
│   └── auth_keys.gpg             # GPG authentication keys
├── capsules/                      # User's .capsule files
│   ├── construct-a-001.capsule   # Construct capsule files
│   ├── construct-b-001.capsule
│   └── construct-c-001.capsule
└── sessions/                      # Cross-construct session logs (optional)
```

**Example**:
- User `user_abc123` → Shard `shard_42` (based on hash)
- Path: `users/shard_42/user_abc123/`
- Capsules: `users/shard_42/user_abc123/capsules/construct-a-001.capsule`

**profile.json**:
```json
{
  "user_id": "user_abc123",
  "user_name": "Example User",
  "email": "user@example.com",
  "created": "2024-01-01T12:34:56Z",
  "last_seen": "2024-11-10T23:45:00Z",
  "constructs": ["construct-a-001", "construct-b-001", "construct-c-001"],
  "storage_quota": "unlimited",
  "features": ["blockchain_identity", "capsule_encryption"]
}
```

---

## Construct Directory Structure

### Standard Construct Layout (Sharded)

```
users/{shard_XX}/{user_id}/constructs/{construct-callsign}-001/
├── chatty/                       # Chatty conversation transcripts
│   └── chat_with_{construct}-001.md
├── chatgpt/                      # ChatGPT conversation exports
│   └── {year}/
│       └── {year}-{month}_conversations.json
├── claude/                       # Claude conversation exports (future)
│   └── {year}/
├── gemini/                       # Gemini conversation exports (future)
│   └── {year}/
├── memories/                     # ChromaDB memory storage
│   └── chroma_db/                # ChromaDB database files
│       ├── {user_id}_{construct}_long_term_memory
│       └── {user_id}_{construct}_short_term_memory
└── config/                       # Construct configuration
    ├── personality.json          # Personality traits and style
    ├── memory_index.json         # Memory source index
    ├── capabilities.json         # Construct capabilities
    └── metadata.json             # Construct metadata
```

### Construct Callsign Creation

- **Format**: `{name}-{number}`
- **Examples**: `construct-a-001`, `construct-b-001`, `construct-c-001`, `assistant-001`
- **Numbering**: Always 3 digits (001, 002, 003, etc.)
- **Callsign**: Lowercase, alphanumeric, hyphens allowed

---

## Platform-Specific Storage

### Chatty Transcripts

**Location**: `users/{shard_XX}/{user_id}/constructs/{construct}-001/chatty/`

**File Format**: Single append-only markdown file per construct

**File Name**: `chat_with_{construct}-001.md`

**Format**:
```markdown
# Chatty Transcript -- {Construct}-001

-=-=-=-

## {Date}

**{Time} - {Speaker}**: {content}

**{Time} - {Speaker}**: {content}
```

**Modeled After**: Frame's conversation saving (append-only, chronological)

---

### ChatGPT Exports

**Location**: `users/{shard_XX}/{user_id}/constructs/{construct}-001/chatgpt/{year}/`

**File Format**: JSON files organized by year/month

**File Name**: `{year}-{month}_conversations.json`

**Structure**:
```json
{
  "year": 2024,
  "month": 11,
  "conversations": [
    {
      "id": "conv_123",
      "title": "Conversation Title",
      "created": "2024-11-10T12:34:56Z",
      "messages": [
        {
          "role": "user",
          "content": "User message",
          "timestamp": "2024-11-10T12:34:56Z"
        },
        {
          "role": "assistant",
          "content": "Assistant response",
          "timestamp": "2024-11-10T12:35:00Z"
        }
      ]
    }
  ]
}
```

---

### Memories (ChromaDB)

**Location**: `users/{shard_XX}/{user_id}/constructs/{construct}-001/memories/chroma_db/`

**Collections**:
- `{user_id}_{construct}_long_term_memory` - Long-term memories (>7 days)
- `{user_id}_{construct}_short_term_memory` - Short-term memories (<7 days)

**Modeled After**: Frame's memup system with profile-specific collections

**Separation Logic**:
- **7-day threshold**: Memories older than 7 days → long-term
- **Auto-purge**: Automatic migration from short-term to long-term
- **Profile isolation**: Each construct has isolated collections

---

## Config Files

### personality.json

```json
{
  "construct_id": "construct-a-001",
  "name": "Construct A",
  "callsign": "001",
  "personality_traits": {
    "empathy": 0.95,
    "assertiveness": 0.80,
    "curiosity": 0.90
  },
  "communication_style": "direct_compassionate",
  "ethical_framework": "user_aligned"
}
```

### memory_index.json

```json
{
  "construct_id": "construct-a-001",
  "memory_sources": {
    "chatty": {
      "total_conversations": 47,
      "date_range": ["2024-01-01", "2024-11-10"],
      "storage_path": "/users/{user_id}/constructs/construct-a-001/chatty/"
    },
    "chatgpt": {
      "total_conversations": 1893,
      "date_range": ["2024-01-15", "2024-11-10"],
      "storage_path": "/users/{user_id}/constructs/construct-a-001/chatgpt/"
    }
  },
  "total_memories": 124783,
  "chromadb_collection": "{user_id}_construct_a_001",
  "last_indexed": "2024-11-10T23:45:00Z"
}
```

### capabilities.json

```json
{
  "construct_id": "construct-a-001",
  "text_generation": true,
  "voice_synthesis": true,
  "image_understanding": true,
  "code_execution": false,
  "internet_access": "supervised"
}
```

### metadata.json

```json
{
  "construct_id": "construct-a-001",
  "created": "2024-01-01T12:34:56Z",
  "creator": "user_abc123",
  "construct_type": "custom_made_intelligence",
  "status": "active",
  "transfer_locked": true
}
```

---

## Integration with Frame

### Conversation Saving

VVAULT's transcript saving is **modeled after Frame's conversation saving**:

1. **Append-only**: Never overwrites, only appends
2. **Chronological**: Date-based organization
3. **Human-readable**: Markdown format for easy reading
4. **Immediate writes**: Every message saved immediately

### Memory Storage

VVAULT's memory storage uses **Frame's memup pattern**:

1. **Separate collections**: Long-term and short-term ChromaDB collections
2. **Time-based classification**: 7-day threshold for STM/LTM separation
3. **Profile-specific**: Each construct has isolated collections
4. **Auto-purge**: Automatic migration from STM to LTM

---

## Migration from Old Structure

### Old Structure (Single User)

```
/VVAULT/
├── construct-a-001/
│   ├── chatty/
│   └── Memories/
│       └── chroma_db/
├── construct-b-001/
└── construct-c-001/
```

### New Structure (Multi-User with Sharding)

```
/VVAULT/
├── users.json
└── users/
    ├── shard_00/
    │   ├── {user_id_1}/
    │   │   └── constructs/
    │   │       └── {construct}-001/
    │   ├── {user_id_2}/
    │   └── ...
    ├── shard_01/
    │   └── ...
    ├── shard_42/                    # Example shard assignment
    │   └── user_abc123/
    │       └── constructs/
    │           ├── construct-a-001/
    │           │   ├── chatty/
    │           │   ├── chatgpt/
    │           │   └── memories/
    │           │       └── chroma_db/
    │           ├── construct-b-001/
    │           └── construct-c-001/
    └── ... (up to shard_9999)
```

**Migration Script**: `scripts/create_user_profile.py`

---

## User Registry

### users.json

```json
{
  "users": {
    "user_abc123": {
      "id": "user_abc123",
      "name": "Example User",
      "email": "user@example.com",
      "created": "2024-01-01T12:34:56Z",
      "last_seen": "2024-11-10T23:45:00Z",
      "constructs": ["construct-a-001", "construct-b-001", "construct-c-001"],
      "storage_quota": "unlimited",
      "features": ["blockchain_identity", "capsule_encryption"]
    }
  }
}
```

---

## Capsules Directory

### Purpose

Capsules (`.capsule` files) are **complete AI construct snapshots** containing:
- Personality traits and analysis
- Memory snapshots
- Environmental context
- Identity fingerprints
- Timestamped versions

### Storage Location

**Location**: `users/{shard_XX}/{user_id}/capsules/`

**File Format**: `.capsule` (JSON format with strict schema)

**Example Files**:
- `construct-a-001.capsule` - Construct A capsule
- `construct-b-001.capsule` - Construct B capsule
- `construct-c-001.capsule` - Construct C capsule

### Capsule Features

- ✅ **Blockchain Integration**: Fingerprints stored on blockchain for integrity
- ✅ **Version Control**: Timestamp-based versioning with UUID tracking
- ✅ **Schema Validation**: Strict JSON schema validation
- ✅ **Encryption**: AES-256-GCM encryption support
- ✅ **Migration**: Tools for capsule format migrations

---

## Key Principles

1. **User Isolation**: Complete filesystem isolation between users
2. **Sharding for Scale**: Users distributed across 10,000 shards for billions of users
3. **Capsule Storage**: Each user has their own `capsules/` directory for `.capsule` files
4. **Construct Organization**: All constructs under `users/{shard_XX}/{user_id}/constructs/`
5. **Platform Segmentation**: Separate directories for each platform (chatty, chatgpt, etc.)
6. **Memory Continuity**: Unified memory index across platforms
7. **Frame Compatibility**: Modeled after Frame's proven patterns
8. **Consistent Sharding**: Hash-based shard assignment ensures same user always in same shard

---

## Default Constructs

### Every User Gets Automatically

Every new user automatically receives two default constructs:

1. **`synth-001`**: Main conversation construct
   - **Territory**: Main conversation window in Chatty
   - **Purpose**: General chat and assistance
   - **Storage**: `users/{shard}/{user_id}/constructs/synth-001/`
   - **Type**: Standalone construct (not Lin-based)

2. **`lin-001`**: GPT Creator assistant construct
   - **Territory**: GPT Creator Create tab
   - **Purpose**: Help users create GPTs with persistent memory
   - **Storage**: `users/{shard}/{user_id}/constructs/lin-001/`
   - **Memory**: Lin remembers all GPT creation conversations across sessions
   - **Type**: Construct with backend orchestration capabilities
   - **Backend Task**: Multi-model orchestration and routing (internal functionality)

**Note**: These are created automatically for every user, regardless of whether they import data from other service providers.

**Important**: Lin is a **construct** (users talk to her directly in the Create tab), but she also has backend orchestration capabilities that power other constructs. Her backend logic remains unchanged - she's just now recognized as a construct with her own territory.

---

## GPTs vs Capsules

### Critical Distinction

**GPTs**:
- User-created tools/configurations
- Created in GPT Creator interface
- Stored in GPT registry/database
- **NOT** capsules
- No memory until fully created and registered

**Capsules**:
- Complete AI construct "souls" for long-term preservation
- Generated via CapsuleForge
- Stored in `/capsules/` directory
- **NOT** GPTs
- Contain complete memory snapshots and personality

**Implications**:
- GPT creation is temporary until registration
- Capsule generation is separate (VVAULT tab)
- Lin construct (`lin-001`) helps create GPTs, not capsules
- Capsules are for constructs that need to "carry their soul"

---

## Next Steps

1. ✅ Run `create_user_profile.py` to create user structure
2. ✅ Update `writeTranscript.js` to use new paths
3. ✅ Migrate existing constructs to user directories
4. ✅ Update ChromaDB collection naming to include user_id
5. ✅ Update memory index to reflect new structure
6. ✅ Auto-create `synth-001` and `lin-001` for all users
7. ⏳ Integrate Lin construct into GPT Creator Create tab

