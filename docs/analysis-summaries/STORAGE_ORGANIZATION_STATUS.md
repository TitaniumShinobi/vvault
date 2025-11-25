# VVAULT Storage Organization Status

**Last Updated**: November 10, 2025

## Overview

This document tracks the status of conversation transcripts and ChromaDB memories storage in VVAULT with optimal user organization.

---

## ✅ Conversation Transcripts

### Status: **FULLY IMPLEMENTED**

**Location**: `users/{shard_XX}/{user_id}/constructs/{construct}-001/chatty/chat_with_{construct}-001.md`

**Implementation**:
- ✅ **Sharded user structure**: Uses `getShardForUser()` to calculate shard (10,000 shards)
- ✅ **User isolation**: Each user's transcripts stored in their own directory
- ✅ **Construct isolation**: Each construct has its own transcript file
- ✅ **Append-only**: Never overwrites, only appends (per CHATTY_VVAULT_TRANSCRIPT_SAVING_RUBRIC.md)
- ✅ **Chronological**: Date-based organization with timestamps
- ✅ **Human-readable**: Markdown format

**File**: `chatty/vvaultConnector/writeTranscript.js`

**Path Structure**:
```
/VVAULT/
└── users/
    └── shard_0000/                    # Shard calculated from user ID hash
        └── devon_690ec2d8c980c59365f284f5/
            └── constructs/
                ├── synth-001/
                │   └── chatty/
                │       └── chat_with_synth-001.md
                ├── lin-001/
                │   └── chatty/
                │       └── chat_with_lin-001.md
                └── nova-001/
                    └── chatty/
                        └── chat_with_nova-001.md
```

**Features**:
- ✅ Backward compatibility: Falls back to old structure if new structure doesn't exist
- ✅ Automatic shard calculation: MD5 hash of user ID → shard assignment
- ✅ Scalable: Supports billions of users (10,000 shards = ~100,000 users per shard)

---

## ⚠️ ChromaDB Memories

### Status: **SPECIFIED BUT NOT FULLY IMPLEMENTED**

**Specified Location**: `users/{shard_XX}/{user_id}/constructs/{construct}-001/memories/chroma_db/`

**Collections**:
- `{user_id}_{construct}_long_term_memory` - Memories older than 7 days
- `{user_id}_{construct}_short_term_memory` - Memories less than 7 days old

**Documentation**: ✅ Fully documented in `VVAULT_FILE_STRUCTURE_SPEC.md`

**Current Implementation Status**:

#### ✅ **Frame Integration** (Python)
- **Location**: `frame/Terminal/Memup/chroma_config.py`
- **Status**: ✅ **ACTIVE** - Points to VVAULT
- **Path**: `VVAULT (macos)/nova-001/Memories/chroma_db`
- **Collections**: Profile-aware collections (`long_term_memory_{profile_id}`)

#### ⚠️ **Chatty Integration** (TypeScript/JavaScript)
- **Status**: ⚠️ **PARTIALLY IMPLEMENTED**
- **Current**: Uses SQLite for STM/LTM (`chatty/src/lib/db.ts`)
- **Gap**: ChromaDB collections not yet initialized in VVAULT structure
- **Needed**: ChromaDB client initialization pointing to VVAULT paths

**What's Missing**:

1. **ChromaDB Client Initialization**:
   - Need to initialize ChromaDB client with VVAULT path
   - Path should be: `users/{shard}/{user_id}/constructs/{construct}/memories/chroma_db/`

2. **Collection Naming**:
   - Collections should follow pattern: `{user_id}_{construct}_long_term_memory`
   - Collections should follow pattern: `{user_id}_{construct}_short_term_memory`

3. **STM/LTM Separation**:
   - 7-day threshold for STM → LTM migration
   - Automatic migration logic

4. **Integration with SynthMemoryOrchestrator**:
   - Currently uses SQLite vault_entries table
   - Should also use ChromaDB for semantic search

---

## Current Storage Architecture

### SQLite (Chatty)
**Location**: `chatty/src/lib/db.ts`

**Tables**:
- `vault_entries` - LTM storage with semantic indexing
- `stm_buffer` - Short-term memory buffer
- `vault_summaries` - Compressed checkpoints

**Status**: ✅ **ACTIVE** - Used by `SynthMemoryOrchestrator`

### ChromaDB (Frame)
**Location**: `frame/Terminal/Memup/chroma_config.py`

**Path**: `VVAULT (macos)/nova-001/Memories/chroma_db`

**Status**: ✅ **ACTIVE** - Used by Frame's memup system

### ChromaDB (Chatty)
**Status**: ⚠️ **NOT YET IMPLEMENTED**

**Needed**: ChromaDB client initialization in Chatty pointing to VVAULT structure

---

## Recommended Implementation

### 1. Create ChromaDB Client Initialization

**File**: `chatty/src/lib/chromadb.ts` (NEW)

```typescript
import { ChromaClient } from 'chromadb';
import path from 'path';

export function getChromaClient(userId: string, constructId: string): ChromaClient {
  const shard = getShardForUser(userId);
  const chromaPath = path.join(
    VVAULT_ROOT,
    'users',
    shard,
    userId,
    'constructs',
    `${constructId}-001`,
    'memories',
    'chroma_db'
  );
  
  return new ChromaClient({
    path: chromaPath,
    settings: {
      anonymized_telemetry: false,
      allow_reset: false
    }
  });
}

export function getCollection(
  userId: string,
  constructId: string,
  memoryType: 'long_term' | 'short_term'
): Collection {
  const client = getChromaClient(userId, constructId);
  const collectionName = `${userId}_${constructId}_${memoryType}_memory`;
  
  return client.getOrCreateCollection({
    name: collectionName,
    metadata: {
      userId,
      constructId,
      memoryType
    }
  });
}
```

### 2. Integrate with SynthMemoryOrchestrator

**File**: `chatty/src/engine/orchestration/SynthMemoryOrchestrator.ts`

**Changes**:
- Add ChromaDB collection access for semantic search
- Use ChromaDB alongside SQLite vault_entries
- Implement 7-day threshold for STM → LTM migration

### 3. Update Memory Index

**File**: `users/{shard}/{user_id}/constructs/{construct}/config/memory_index.json`

**Add**:
```json
{
  "chromadb_collections": {
    "long_term": "{user_id}_{construct}_long_term_memory",
    "short_term": "{user_id}_{construct}_short_term_memory"
  },
  "chromadb_path": "users/{shard}/{user_id}/constructs/{construct}/memories/chroma_db/"
}
```

---

## Summary

| Component | Status | Location | Notes |
|-----------|--------|----------|-------|
| **Transcripts** | ✅ **FULLY IMPLEMENTED** | `users/{shard}/{user_id}/constructs/{construct}/chatty/` | Sharded, append-only, optimal organization |
| **ChromaDB (Frame)** | ✅ **ACTIVE** | `VVAULT (macos)/nova-001/Memories/chroma_db` | Working, but uses old path structure |
| **ChromaDB (Chatty)** | ⚠️ **NOT IMPLEMENTED** | Should be: `users/{shard}/{user_id}/constructs/{construct}/memories/chroma_db/` | Needs implementation |
| **SQLite (Chatty)** | ✅ **ACTIVE** | In-memory + browser storage | Used for STM/LTM, but not ChromaDB |

---

## Next Steps

1. ✅ **Transcripts**: Already optimal - no changes needed
2. ⏳ **ChromaDB**: Implement client initialization pointing to VVAULT structure
3. ⏳ **Integration**: Connect ChromaDB to SynthMemoryOrchestrator
4. ⏳ **Migration**: Migrate Frame's ChromaDB to new sharded structure
5. ⏳ **Testing**: Verify ChromaDB collections are created in correct locations

---

**Transcripts are optimally organized. ChromaDB needs implementation to match the specified structure.**

