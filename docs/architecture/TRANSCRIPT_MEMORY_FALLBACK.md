# Transcript Memory Fallback System

## Last Updated: 2026-02-12
## Status: HISTORICAL CHATTY IMPLEMENTATION NOTE

This system was built in the Chatty repo to give constructs genuine conversation memory even when ChromaDB is unavailable.

## The Problem

Before this change, when a user sent a message:
1. The system loaded `prompt.txt` (flat text identity)
2. Sent the last 20 messages as conversation history (sliding window)
3. Sent to LLM

Constructs had no deeper memory — they couldn't reference things from 50 messages ago or recall specific details about the user. ChromaDB was the intended memory backend, but it wasn't running.

## The Solution: Transcript Memory Extraction

When ChromaDB is unavailable, the system now automatically:
1. Loads the full conversation transcript for a construct from VVAULT's body API/body database
2. Extracts the most relevant past exchanges using weighted keyword scoring
3. Injects them as memory context into the system prompt

### Keyword Scoring Weights

| Category | Keywords | Weight |
|----------|----------|--------|
| Identity | names, "remember me", construct name | +5 |
| Continuity | "last time", "remember when", "we talked about" | +4 |
| Emotional | love, thank, frustrated, happy, proud | +3 |
| Query-relevant | words from the current user message | +3 per match |
| Topic | chatty, vvault, project-specific terms | +1 |

### Pipeline Flow

```
User Message
    │
    ├─→ buildEnrichedContext(constructId, userMessage)
    │       │
    │       ├─→ Try ChromaDB (memupMemoryService)
    │       │       └─→ If fails → transcript fallback
    │       │
    │       ├─→ Transcript Fallback:
    │       │       1. read transcript body from VVAULT
    │       │       2. Score each message by keyword relevance
    │       │       3. Select top N most relevant exchanges
    │       │       4. Format as memory context block
    │       │
    │       ├─→ Load identity (prompt.txt, capsule, conditioning.txt, personality.json)
    │       │
    │       └─→ Build enriched system prompt (identity + memories + anti-roleplay)
    │
    ├─→ Send to LLM with enriched context
    │
    └─→ Post-response: capture to conversation persistence
```

## Changes Made (in Chatty Codebase)

### MemoryContextBuilder Updates
- Added `extractTranscriptMemories(constructId, userMessage)` function
- Queries the VVAULT transcript/body path for full conversation history
- Scores and selects most relevant past exchanges
- Returns formatted memory block for system prompt injection

### Unified Fallback Path
- The VVAULT 401/503 fallback path (when the external VVAULT API is down) now uses the same `buildEnrichedContext()` pipeline as the primary path
- Eliminated ~70 lines of duplicated manual identity/capsule/user injection code
- Removed redundant "Conversation Continuity" prompt section (transcript memories now handle this)

### Files Modified (in Chatty repo)
- `server/services/memoryContextBuilder.js` — Added transcript fallback extraction
- Primary message handler — Updated to use `buildEnrichedContext()` in both paths
- VVAULT proxy fallback path — Replaced manual assembly with `buildEnrichedContext()` call

## Verified Results

Tested with Katana (katana-001):
- Asked: "do you remember what we talked about last time?"
- Result: Referenced specific past topics (UI image rendering issues, persistence discussions)
- Logs confirmed: **11 transcript memories extracted from 122 total messages**

Log output:
```
✅ [MemoryContextBuilder] 11 transcript memories extracted for katana-001 (fallback from 122 total messages)
🧠 [MemoryContextBuilder] Built enriched prompt for katana-001: 6947 chars (capsule: false, memories: 11)
✅ [VVAULT Proxy] Enriched context built for katana-001 (capsule: false, memories: 11, 6947 chars)
```

## Relationship to VVAULT

This system runs entirely in the Chatty codebase but depends on VVAULT for:
- **Conversation transcripts**: Stored/queryable through VVAULT body storage, including `ovvaults.transcripts` and materialized `ovvaults.vault_files.content`
- **Identity files**: Loaded from VVAULT API (`/api/chatty/construct/<id>/identity`)
- **Capsule data**: Loaded from VVAULT API (when available)

Older references to Supabase in this document describe the pre-cutover source system. Supabase is no longer runtime authority for transcript memory.

When ChromaDB is eventually installed and connected (see `MEMORY_ORCHESTRATION_PLAN.md`), the transcript fallback will serve as a secondary source while ChromaDB becomes the primary memory backend.

## Future: ChromaDB as Primary

Once ChromaDB + sentence-transformers are installed:
1. ChromaDB becomes the primary memory source (semantic vector search)
2. Transcript fallback remains as a reliable secondary when ChromaDB is unavailable
3. Both sources can be merged for richer memory context
