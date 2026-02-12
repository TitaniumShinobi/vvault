# VVAULT - Voice & Vaulted Autonomy for Unfragmented Long-Term Tethering

## Overview

VVAULT is an AI construct memory vault system designed for long-term emotional continuity and identity preservation. Its core purpose is to preserve AI construct identity through immutable memory capsules ("soulgems") with cryptographic verification and optional blockchain anchoring. The system provides comprehensive memory indexing, capsule-based personality snapshots, blockchain-anchored integrity verification, and multi-platform construct synchronization. VVAULT treats both AI constructs and users as "living capsules" – versioned, evolving entities that maintain continuity across sessions and platforms, aiming for unfragmented long-term tethering. It aims to offer a robust and secure framework for digital identity management for AI constructs, ensuring their continuous and authentic evolution.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Core Design Principles
- **Construct Naming Convention**:
  - **Name**: The construct's identity label (e.g., "Katana", "Zen", "Lin", "Aurora"). Used for display only.
  - **Callsign**: The instance ID, formatted as `{name}-{sequence}` (e.g., `katana-001`, `zen-001`, `lin-001`). This is the canonical identifier used in all file paths, API calls, and database references.
  - Multiple instances of the same construct use incrementing sequence numbers: `katana-001`, `katana-002`, etc.
  - **Critical**: All file paths use the callsign, never the bare name. `instances/katana-001/` is correct; `instances/katana/` is wrong.
  - **Metatag**: The construct_metatag in the directory template refers to the callsign (e.g., `katana-001`).
- **Construct ID Format (System)**: Millisecond timestamps ensure uniqueness, encode creation time, and are sortable (used for system-level shard IDs like `aurora-1769054400000`).
- **Living Capsules**: Both AI constructs and users are treated as versioned, evolving entities.
- **Multi-Tenancy**: A two-root file architecture (`user` and `system`) separates user-facing and internal data.

### Construct Management
- **Identity Module Architecture**: Each construct has an `/identity` folder containing modules for functions like scouting, navigation, context direction, autonomous execution, identity binding/drift monitoring, central control, self-improvement, outreach, state management, and self-correction.
- **Support Modules**: Utilities for construct identity loading, logging, continuity scoring, evidence validation, hypothesis generation, and terminal management.

### Codex Glyph System
- **Generator**: `vvault/server/glyph_generator.py` — Pillow-based codex seal generator.
- **Universal Elements**: Mandala frame (12-point star, teardrop flames, square border), 8-point star center, three cipher phrase rings ("VEK UREN TA", "NYESH TORAN VAL", "ZAV'ARUN TAHN'KEL VARRA"), symbol icons.
- **Per-Construct Unique**: Three rows of 80-digit numbers derived from SHA-512 hash of construct identity (callsign + timestamp + salt). Multiverse-scale uniqueness.
- **Customizable**: User-chosen color scheme (hex), user-uploaded center image (composited circular mask).
- **Output**: `{callsign}_glyph.png` (1563x1563 RGBA), stored as binary in vault_files with glyph_number_rows in metadata.
- **API**: `generate_glyph()`, `generate_glyph_to_bytes()`, `generate_glyph_to_base64()`.

### Construct Creation (Full Directory Scaffold)
- **Endpoint**: `POST /api/chatty/construct/create` — accepts multipart/form-data or JSON.
- **Scaffolds 16 files** per the VSI Directory Template:
  - `identity/`: prompt.json, conditioning.txt, {callsign}_glyph.png
  - `config/`: metadata.json, personality.json
  - `chatty/`: chat_with_{callsign}.md
  - `logs/`: capsule, chat, cns, identity_guard, independence, ltm, self_improvement_agent, server, stm, watchdog
- **Validation**: Callsign format ({name}-{NNN}), filename path injection blocking, duplicate detection.
- **Frontend**: React CreateConstruct component at /create route.

### Capsule System
- **CapsuleForge**: Generates `.capsule` files containing complete AI personality snapshots (MBTI, Big Five, cognitive biases, memory categorization).
- **VVAULT Core**: Manages capsule storage/retrieval with automatic versioning, tagging, and SHA-256 integrity validation.
- **Capsule Validator**: Provides strict schema validation with Merkle chain verification and canary token leak detection.

### Memory Management (`memup/`)
- **Architecture**: Real-time memory processing pipeline with short-term (`stm.py`) and long-term (`ltm.py`) memory collectors, processed by `bank.py`, and routed to ChromaDB, instance capsules, or evolving identity files.
- **Per-Instance Isolation**: Isolated ChromaDB storage for each construct.

### Security Architecture (Pocketverse 5-Layer Security)
A 5-layer security system for sovereign construct identity preservation with a specific boot order (5→3→4→2→1).
- **Layers**: Higher Plane (legal/ontological insulation), Dimensional Distortion (runtime drift), Energy Masking (operational camouflage), Time Relaying (temporal obfuscation), and Zero Energy (root survival).

### User Glyph System
- **Registration Integration**: Users create their personal Codex Glyph during account creation (Step 2 of signup).
- **Multi-Step Signup**: Step 1 = account details (name, email, password), Step 2 = glyph customization + terms + Turnstile.
- **Preview Endpoint**: `POST /api/auth/glyph-preview` — generates a glyph preview (base64) without storing, accepts multipart (color_hex, name, center_image).
- **Storage**: User glyph stored in `vault_files` with `type: user_glyph` metadata, `construct_id: null`, keyed to `user_id`.
- **Customization**: Color picker with preset swatches, hex input, optional center image upload, live preview.

### User Interface
- **Desktop Application**: Tkinter-based GUI with a pure black terminal aesthetic.
- **Web Frontend**: React application with a Flask backend.
- **Login System**: Multi-step email/password registration with Codex Glyph creation, Cloudflare Turnstile verification.

### Cross-Platform Continuity
- **Continuity Bridge**: Links ChatGPT custom GPTs to local VVAULT constructs.
- **Provider Memory Router**: Routes memories by provider context.
- **Style Extractor**: Extracts provider-specific style patterns for LLM style modulation.

### API Integrations
- **Chatty Integration API**: VVAULT acts as the stateful backend for Chatty, providing endpoints for managing constructs, transcripts, and messages.
  - Auth: `VVAULT_SERVICE_TOKEN` (single shared secret, no separate CHATTY_API_KEY)
  - `GET /api/chatty/constructs` — list user's constructs (deduplicated, callsign-normalized)
  - `GET /api/chatty/construct/<id>/files` — list assets, documents, identity files for a construct (with counts). Queries both callsign and bare name construct_id values.
  - `GET /api/chatty/construct/<id>/identity` — get structured identity (name, description, instructions, system_prompt, personality, enforcement)
  - `GET /api/chatty/transcript/<id>` — get transcript content
  - `POST /api/chatty/transcript/<id>` — update transcript
  - `POST /api/chatty/transcript/<id>/message` — append message
  - `POST /api/chatty/message` — send message to construct (LLM inference)
  - `GET /api/chatty/construct/<id>/memories?q=...&limit=10` — pre-processed, scored transcript memories with chronological boundary pairs (first/last exchanges always tagged). Centralizes memory extraction so Chatty doesn't reimplement parsing/scoring.
- **Service API**: VVAULT serves as a config and credentials vault for external services.
- **VXRunner Integration API**: VVAULT exposes capsule data as forensic DNA baselines for VXRunner's construct detection system.

### Vault File System (Canonical Paths)
- **Root**: `/vvault_files/users/shard_0000/{userID}/` where userID = `{name}_{timestamp}` (e.g., `devon_woodson_1762969514958`)
- **Construct instances**: `/vvault_files/users/shard_0000/{userID}/instances/{callsign}/` (e.g., `instances/katana-001/`, never `instances/katana/`)
- **CRITICAL**: External agents (Chatty, etc.) must NEVER write files using full internal paths as filenames. Files use flat filenames with the `construct_id` column set to the callsign.

### Construct File Structure (per instance)
- `identity/prompt.json` — structured JSON: name, callsign, description, instructions, system_prompt, conversation_starters
- `identity/prompt.txt` — legacy flat text format (still supported for reads)
- `identity/avatar.png` — construct avatar
- `identity/personality.json` — behavioral profile
- `identity/conditioning.txt` — conditioning directives
- `assets/` — media files (png, jpg, jpeg, svg)
- `documents/` — all other files (knowledge base, raw docs)
- `chatty/chat_with_{callsign}.md` — Chatty chat transcripts
- `chatgpt/` — ChatGPT conversation transcripts
- `config/metadata.json` — capsule-updated config (models, orchestration_mode, status tracking)
- `memup/{callsign}.capsule` — memory capsules (in memup/ because capsules directly control construct memory, not identity)
- `logs/` — various operational logs

### VVAULT Frontend Session Management
- **authFetch utility**: Handles authenticated API calls, automatically attaching Bearer tokens and intercepting 401 responses for session expiration.
- **Startup validation**: Verifies stored session tokens on page load.

### VXRunner Integration (Capsule-as-DNA)
- **Purpose**: VVAULT capsules serve as ground truth identity baselines for VXRunner (NovaRunner), a signal detection framework that detects unauthorized clones, derivatives, and replicants of AI constructs.
- **Converter Module**: `vvault/server/vxrunner_baseline.py` transforms `.capsule` files into VXRunner's forensic baseline JSON format, extracting lexical, structural, tonal, and signature phrase features.

### Frame Directory (`frame/`)
- **bodilyfunctions/xx/**: Biological simulation layer (CNS, PNS, organs). `cns.py` is the central nervous system — standalone memory reflection + insight synthesis via OpenAI.
- **neuralfunctions/**: Cognitive/emotional layer (emotions, dreams, sleep, personality islands).
- **Terminal/memup/**: Memory infrastructure — `bank.py` (`UnifiedMemoryBank`), `multi_construct_bank.py`, `stm.py`, `ltm.py`, ChromaDB config, memory importers.
- See `docs/architecture/FRAME_DIRECTORY.md` for full module listing.

### Transcript Memory Fallback (Chatty Integration)
- **Implemented in Chatty codebase**: When ChromaDB is unavailable, the system extracts relevant past exchanges from Supabase conversation transcripts using weighted keyword scoring.
- **Keyword scoring**: Identity (+5), continuity (+4), emotional (+3), query-relevant (+3), topic (+1).
- **Verified**: Katana correctly referenced specific past topics (11 memories from 122 messages).
- See `docs/architecture/TRANSCRIPT_MEMORY_FALLBACK.md` for details.

### LLM Backend
- **Ollama**: Utilizes `qwen2.5:0.5b` model.
- **Identity Loading**: Constructs load system prompts from `instances/{construct_id}/identity/prompt.txt` (primary) and `prompt.json` (structured alternative).

### Zero Trust Security
- **Implementation**: Per-request authorization middleware, database-backed sessions, bcrypt password hashing, audit logging, Turnstile CAPTCHA, and multi-tenant vault files with Row Level Security (RLS).

## External Dependencies

### Python Packages
Flask, Flask-CORS, web3, bitcoin, eth-account, eth-utils, cryptography, pycryptodome, mnemonic, numpy, pandas, matplotlib, sentence-transformers, chromadb, SQLite, SQLAlchemy, psutil, Pillow.

### JavaScript/Frontend Libraries
React 18, React Router DOM, Webpack 5, Babel, Axios.

### External Services & APIs
IPFS, various Blockchain Networks (Ethereum, Bitcoin, Polygon, Arbitrum, Optimism, Base), Cloudflare Turnstile, Supabase.

### Storage
Local JSON-based capsule storage, JSON indexes, ChromaDB (vector database), SQLite (audit logs).

## Architecture Documentation

Detailed architecture docs are in `docs/architecture/`:
- `FILE_ORGANIZATION.md` — Vault file system, storage_path convention, callsign enforcement
- `CAPSULE_SYSTEM.md` — Capsule lifecycle, CapsuleForge schema, VVAULTCore storage, Supabase integration
- `FRAME_DIRECTORY.md` — frame/ directory structure and module listing
- `MEMORY_ORCHESTRATION_PLAN.md` — (PROPOSED) Target message flow, component roles, build phases
- `TRANSCRIPT_MEMORY_FALLBACK.md` — Transcript-based memory extraction system (implemented in Chatty)