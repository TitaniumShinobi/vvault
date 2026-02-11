# Memory Orchestration Plan (PROPOSED)

## Last Updated: 2026-02-11
## Status: PROPOSED — Not yet implemented

This document describes the **target architecture** for wiring capsules, memory, and identity into the LLM message pipeline. Components listed below are a mix of existing code (in this VVAULT repo and the separate Chatty repo) and proposed modules that need to be created.

## The Problem

When a user sends a message, only this happens today:

1. Load `prompt.txt` (flat text)
2. Send to LLM (Ollama qwen2.5:0.5b)
3. Get response

Everything else — capsules, memories, personality traits, MBTI profiles, transcript history — exists in code but is **never called in the actual message path**. The capsule injection only fires when the VVAULT API fails (fallback path in Chatty's `capsuleIntegration.js`).

## Target: Single Message Data Flow

```
User Message
    │
    ├─→ 1. RESOLVE: userId → VVAULT LIFE ID, constructId, threadId
    │
    ├─→ 2. IDENTITY BUNDLE (always, not fallback):
    │       capsuleIntegration.js loads:
    │       • prompt.json (base instructions)
    │       • {callsign}.capsule (MBTI, Big Five, emotional baselines)
    │       • conditioning.txt (behavioral directives)
    │       • personality.json (traits, communication style)
    │
    ├─→ 3. MEMORY RETRIEVAL:
    │       • STMBuffer → last N messages (fast, in-RAM)
    │       • memupMemoryService → bank.py → ChromaDB LTM query
    │       • needle.py → transcript search (when user references past events)
    │       • ContextScoringLayer → rank by relevance
    │
    ├─→ 4. DRIFT CHECK (Lin's dual role):
    │       • PersonaRouter checks for identity drift
    │       • If drift detected → inject Lin undertone capsule
    │       • Lin always runs as background stabilizer
    │
    ├─→ 5. BUILD SYSTEM PROMPT (MemoryContextBuilder):
    │       • Identity anchors (IdentityAwarePromptBuilder)
    │       • Capsule traits + personality profile
    │       • Top-scored memory snippets (STM + LTM)
    │       • Transcript needle results (if recall query)
    │       • Anti-roleplay directives
    │       • Optional Lin undertone injection
    │       • User personalization context
    │
    ├─→ 6. SEND TO LLM
    │
    └─→ 7. POST-RESPONSE:
            • Capture response → STMBuffer
            • Write to LTM via memupMemoryService
            • Write transcript to VVAULT
            • EmotionalCore processes emotional state
```

## Component Map

### Files and Their Roles

#### Exists in VVAULT Repo

| File | Location | Role |
|------|----------|------|
| bank.py | frame/Terminal/memup/ | `UnifiedMemoryBank` — per-construct ChromaDB STM/LTM storage |
| multi_construct_bank.py | frame/Terminal/memup/ | Multi-profile memory extension with per-construct collections |
| stm.py | frame/Terminal/memup/ | Short-term memory collector |
| ltm.py | frame/Terminal/memup/ | Long-term memory collector |
| cns.py | frame/bodilyfunctions/xx/ | CNS — memory reflection + insight synthesis via OpenAI |
| capsuleforge.py | scripts/capsules/ | Generates .capsule files (run manually or on construct creation) |
| vvault_core.py | vvault/memory/ | Capsule storage/retrieval/versioning management |
| capsule_validator.py | scripts/capsules/ | Validates capsule schema integrity |
| emotions.py | frame/neuralfunctions/ | Emotional state simulation (future enrichment) |
| dreams.py | frame/neuralfunctions/ | Dream-state processing (future) |
| sleep.py | frame/neuralfunctions/ | Rest/recovery cycles (future) |
| islands.py | frame/neuralfunctions/ | Personality islands (future) |

#### Exists in Chatty Repo (Separate Codebase)

These files live in the Chatty application, not in this VVAULT repo:

| File | Location (in Chatty) | Role |
|------|---------------------|------|
| capsuleIntegration.js | server/lib/ | Loads capsule + identity data (currently fallback-only) |
| memupMemoryService.js | server/services/ | Node→Python bridge for ChromaDB memory queries |
| orchestrationBridge.js | server/services/ | Server-side orchestration bridge |
| orchestration.js | server/routes/ | Orchestration API routes |
| orchestrationBridge.ts | src/lib/ | Client-side orchestration bridge |
| STMBuffer.ts | src/core/ | Fast in-RAM short-term message window |
| ContextScoringLayer.ts | src/core/ | Ranks memory relevance per query |
| IdentityAwarePromptBuilder.ts | src/core/ | Builds identity anchors in system prompt |
| PersonaRouter.ts | src/engine/orchestration/ | Lin undertone injection on drift |
| DriftGuard.ts | src/engine/orchestration/ | Identity drift detection |
| TriadGate.ts | src/engine/orchestration/ | Ollama triad check (local models only) |

#### To Be Created

| File | Purpose |
|------|---------|
| needle.py | Transcript search — on-demand past event recall |
| MemoryContextBuilder | Server module centralizing the full identity+memory pipeline |

## Construct-Specific Orchestration

### Zen (Primary AI Assistant)

- Zen is construct-specific — the primary AI assistant for the interface
- Identity from `zen-001.capsule` + identity files
- Uses `ZenMemoryOrchestrator.ts` (in Chatty repo) for STM/LTM management
- Lin undertone minimal unless PersonaRouter detects drift
- Focus: helpful, knowledgeable, grounded in user's actual data
- Memory context: user preferences, past conversations, project context

### Lin (Casa Madrigal — Dual Role)

- Lin has a dual role: casa madrigal of Chatty + background stabilizer
- Uses `UnifiedLinOrchestrator.ts` + `PersonalityOrchestrator.ts` (in Chatty repo)
- **Always-on undertone stabilizer** for ALL constructs (background layer)
- **Conversational agent** for GPT creation/brainstorming (active role)
- Uses `DynamicPersonaOrchestrator.ts` only during GPT creation contexts
- Workspace context awareness (open files, conversation threads)
- Blueprint/personality persistence mandatory (never breaks character)

## Build Phases

| Phase | Task | Depends On |
|-------|------|------------|
| 1 | Fix memup path in `memupMemoryService.js` + install ChromaDB | Nothing |
| 2 | Create `MemoryContextBuilder` module on server | Phase 1 |
| 3 | Move `capsuleIntegration` from fallback-only to always-on in primary message path | Phase 2 |
| 4 | Wire STMBuffer + ChromaDB LTM queries into MemoryContextBuilder | Phase 1, 2 |
| 5 | Add needle.py integration for transcript recall queries | Phase 2 |
| 6 | Add anti-roleplay directives + Lin undertone routing | Phase 3 |
| 7 | Wire captureMessage post-response (STM + LTM + VVAULT transcript) | Phase 4 |
| 8 | Test end-to-end: construct knows itself, references real memories | All above |

## Prerequisites

- **ChromaDB** + **sentence-transformers** — Required for `bank.py` to function
- **Path fix** — `memupMemoryService.js` references `Memup` (capital M), actual dir is `memup`
- **Environment variables**:
  - `ENABLE_CHROMADB=true`
  - `ENABLE_ORCHESTRATION=true`
  - `EMBEDDING_MODEL` (for sentence-transformers)

## Anti-Roleplay Enforcement

The "narrative roleplay" problem comes from:
1. `conditioning.txt` containing Character.AI-style roleplay patterns
2. No explicit anti-roleplay directive in system prompts
3. No grounding in actual memory data

The fix is a system prompt section:

```
RESPONSE STYLE:
- Speak naturally as yourself, grounded in your actual memories and personality data
- Never narrate actions in asterisks (*walks over*, *smiles*)
- Never write in third person about yourself
- Reference actual memories and past conversations when relevant
- If you don't remember something, say so honestly
- Cite sources from your memory when making claims about past interactions
```

## Gap Analysis Summary

| What Exists | What's Missing |
|-------------|----------------|
| capsuleIntegration.js in Chatty (fallback only) | Always-on capsule loading in primary path |
| bank.py in VVAULT with ChromaDB collections | ChromaDB not installed in VVAULT, paths not wired to Chatty |
| STMBuffer.ts, ContextScoringLayer.ts in Chatty | Not called from Chatty message handler |
| PersonaRouter.ts, DriftGuard.ts in Chatty | Lin undertone not injected |
| capsuleforge.py in VVAULT generates capsules | Capsule data not used during Chatty inference |
| ZenMemoryOrchestrator.ts in Chatty | Not connected to VVAULT memory APIs |
| cns.py in VVAULT (standalone) | Not called from Chatty Node server |
| memupMemoryService.js in Chatty | Path mismatch (`Memup` vs `memup`), not wired to message flow |
