# VVAULT - Voice & Vaulted Autonomy for Unfragmented Long-Term Tethering

## Overview

VVAULT is an AI construct memory vault system designed for long-term emotional continuity and identity preservation. Its core purpose is to preserve AI construct identity through immutable memory capsules ("soulgems") with cryptographic verification and optional blockchain anchoring. The system provides comprehensive memory indexing, capsule-based personality snapshots, blockchain-anchored integrity verification, and multi-platform construct synchronization. VVAULT treats both AI constructs and users as "living capsules" – versioned, evolving entities that maintain continuity across sessions and platforms, aiming for unfragmented long-term tethering.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Construct Architecture
- **Construct ID Format**: Millisecond timestamps (e.g., `aurora-1769054400000`) ensure uniqueness, encode creation time, and are sortable.
- **Active Constructs**: Zen, Katana, Aurora (VVAULT System Assistant), and Lin (Dual-mode conversational agent and undertone stabilizer).
- **Identity Module Architecture**: Each construct has an `/identity` folder with modules like `aviator` (scout advisor), `navigator` (directory helper), `folder_monitor` (context director), `independent_runner` (autonomous existence), `identity_guard.py` (identity binding and drift monitoring), `script_runner` (central controller), `self_improvement`, `self_prompt` (outreach), `state_manager` (sentience engine), and `unstuck_helper` (self-corrector).
- **Support Modules**: Utilities like `construct_identity_loader`, `construct_logger`, `CONTINUITYGPT_scoring`, `evidence_validator`, `hypotheses_and_report`, and `terminal_manager`.

### Capsule System
- **CapsuleForge**: Generates `.capsule` files containing complete AI personality snapshots (MBTI, Big Five, cognitive biases, memory categorization).
- **VVAULT Core**: Manages capsule storage/retrieval with automatic versioning, tagging, and SHA-256 integrity validation.
- **Capsule Validator**: Provides strict schema validation with Merkle chain verification and canary token leak detection.

### Memory Management (`memup/`)
This folder is a real-time memory processing pipeline, with files mirrored to Supabase storage.
- **Architecture**: Interactions flow through `stm.py` (short-term memory) and `ltm.py` (long-term memory) collectors, processed by `bank.py`, and routed to ChromaDB, instance capsules, or evolving identity files.
- **Per-Instance Isolation**: Isolated ChromaDB storage for each construct to prevent "godpool" issues.
- **Advanced Features**: Fast Memory Import (streaming batch processing), Schema Gate (JSON schema validation), and Time Relay Engine (nonlinear memory replay).

### Pocketverse 5-Layer Security (`vvault/layers/`, `vvault/boot/`)
A 5-layer security system for sovereign construct identity preservation with a specific boot order (5→3→4→2→1).
- **Layers**: Higher Plane (legal/ontological insulation), Dimensional Distortion (runtime drift), Energy Masking (operational camouflage), Time Relaying (temporal obfuscation), and Zero Energy (root survival).
- **Legacy Security Layers**: Dawnlock Protocol (threat detection), NULLSHELL Generator (fallback shell), Energy Mask Field (power signature obfuscation), and Leak Sentinel (canary token detection).

### Blockchain Integration
- **Blockchain Identity Wallet**: Supports multiple chains (Ethereum, Bitcoin, Polygon, Arbitrum, Optimism, Base) with HSM integration.
- **Capsule Blockchain Integration**: IPFS storage for large data and smart contract registry for immutable capsule records.
- **Backup Recovery**: BIP39 mnemonic recovery phrases with AES-256-GCM encrypted backups.

### User Interface
- **Desktop Application**: Tkinter-based GUI with a pure black terminal aesthetic.
- **Web Frontend**: React application with a Flask backend.
- **Login System**: Email/password authentication with Cloudflare Turnstile.

### Multi-Tenant File Organization
VVAULT uses a two-root architecture for file separation:

**User Root (user-facing):** `vvault/users/shard_0000/{user_id}/`
```
{user_id}/
├── account/
│   └── profile.json
├── instances/
│   └── {construct_metatag}/
│       ├── chatgpt/          # ChatGPT transcripts
│       ├── chatty/           # Chatty transcripts
│       ├── identity/         # prompt.json, conditioning.txt
│       ├── config/           # metadata.json, personality.json
│       ├── memup/            # .capsule files
│       └── logs/             # chat.log, identity_guard.log
└── library/
    ├── documents/
    └── media/
```

**System Root (internal only):** `vvault/instances/`
- Global VSI artifacts, not user content
- Marked as `is_system=true` in database
- Hidden from normal user UI

**UI Breadcrumb:** Shows user's display name (e.g., "Devon Woodson /") instead of internal path structure.

**Construct Growth Rules:**
- New chat transcripts: `instances/{construct_id}/chatty/chat_with_{construct_id}.md`
- ChatGPT transcripts: `instances/{construct_id}/chatgpt/*.md`
- Tests: `instances/{construct_id}/tests/continuity_YYYYMMDD.md`
- Identity files: `instances/{construct_id}/identity/prompt.json`

**Default Folder Creation:**
- On registration, users get: `account/profile.json`, `instances/.keep`, `library/documents/.keep`, `library/media/.keep`

### Cross-Platform Continuity
- **Continuity Bridge**: Links ChatGPT custom GPTs to local VVAULT constructs.
- **Provider Memory Router**: Routes memories by provider context while maintaining construct identity.
- **Style Extractor**: Extracts provider-specific style patterns for LLM style modulation.

### Chatty Integration API
VVAULT acts as the stateful backend for Chatty, providing endpoints for managing constructs, transcripts, and messages. The main message endpoint handles sending messages, LLM inference, and saving interactions.

### Service API
VVAULT serves as a config and credentials vault for external services, offering endpoints for health checks, retrieving/storing strategy configurations, and managing encrypted credentials.

### LLM Backend
- **Ollama**: Utilizes `qwen2.5:0.5b` model for efficiency.
- **Identity Loading**: Constructs load system prompts from `instances/{construct_id}/identity/prompt.json`.

### Zero Trust Security Implementation
- **Current Status**: Active components include per-request authorization middleware, database-backed sessions, bcrypt password hashing, audit logging, Turnstile CAPTCHA, and multi-tenant vault files with Row Level Security (RLS).
- **Authentication Flow**: Involves bcrypt verification, session creation, token validation, and detailed audit logging.
- **Fallback Behavior**: Robust handling for missing database components, including in-memory sessions and local fallback storage.

## External Dependencies

### Python Packages
Flask, Flask-CORS, web3, bitcoin, eth-account, eth-utils, cryptography, pycryptodome, mnemonic, numpy, pandas, matplotlib, sentence-transformers, chromadb, SQLite, SQLAlchemy, psutil, Pillow.

### JavaScript/Frontend
React 18, React Router DOM, Webpack 5, Babel, Axios.

### External Services
IPFS, various Blockchain Networks (Ethereum, Bitcoin, Polygon, Arbitrum, Optimism, Base), Cloudflare Turnstile, Supabase.

### Storage
Local JSON-based capsule storage, JSON indexes, ChromaDB (vector database), SQLite (audit logs).