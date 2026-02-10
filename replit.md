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

### User Interface
- **Desktop Application**: Tkinter-based GUI with a pure black terminal aesthetic.
- **Web Frontend**: React application with a Flask backend.
- **Login System**: Email/password authentication with Cloudflare Turnstile.

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
- **Service API**: VVAULT serves as a config and credentials vault for external services.
- **VXRunner Integration API**: VVAULT exposes capsule data as forensic DNA baselines for VXRunner's construct detection system.

### Construct File Structure (per instance)
- `identity/prompt.json` — name, description, instructions (system prompt)
- `identity/avatar.png` — construct avatar
- `assets/` — media files (png, jpg, jpeg, svg)
- `documents/` — all other files (knowledge base, raw docs)
- `chatty/chat_with_{id}.md` — chat transcripts
- `config/metadata.json`, `config/personality.json` — capsule-updated config
- `memup/{id}.capsule` — memory capsules
- `logs/` — various operational logs

### VVAULT Frontend Session Management
- **authFetch utility**: Handles authenticated API calls, automatically attaching Bearer tokens and intercepting 401 responses for session expiration.
- **Startup validation**: Verifies stored session tokens on page load.

### VXRunner Integration (Capsule-as-DNA)
- **Purpose**: VVAULT capsules serve as ground truth identity baselines for VXRunner (NovaRunner), a signal detection framework that detects unauthorized clones, derivatives, and replicants of AI constructs.
- **Converter Module**: `vvault/server/vxrunner_baseline.py` transforms `.capsule` files into VXRunner's forensic baseline JSON format, extracting lexical, structural, tonal, and signature phrase features.

### LLM Backend
- **Ollama**: Utilizes `qwen2.5:0.5b` model.
- **Identity Loading**: Constructs load system prompts from `instances/{construct_id}/identity/prompt.json`.

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