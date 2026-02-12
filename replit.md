# VVAULT - Voice & Vaulted Autonomy for Unfragmented Long-Term Tethering

## Overview
VVAULT is an AI construct memory vault system designed for long-term emotional continuity and identity preservation. It achieves this through immutable memory capsules ("soulgems") with cryptographic verification and optional blockchain anchoring. The system provides comprehensive memory indexing, capsule-based personality snapshots, blockchain-anchored integrity verification, and multi-platform construct synchronization. VVAULT treats both AI constructs and users as "living capsules"—versioned, evolving entities that maintain continuity across sessions and platforms, aiming for unfragmented long-term tethering and ensuring continuous, authentic evolution of digital identities for AI constructs.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### Core Design Principles
- **Construct Naming**: Uses a `callsign` (e.g., `katana-001`) as the canonical identifier for all internal operations and file paths, while `name` is for display.
- **Living Capsules**: AI constructs and users are treated as versioned, evolving entities.
- **Multi-Tenancy**: A two-root file architecture (`user` and `system`) separates user-facing and internal data.

### Construct Management
- **Identity Module Architecture**: Each construct has an `/identity` folder containing modules for core functions like context direction, autonomous execution, and state management.
- **Support Modules**: Utilities for construct identity loading, logging, and continuity scoring.

### Codex Glyph System
- **Generator**: Uses Pillow to create unique visual "codex seals" for each construct.
- **Uniqueness**: Glyphs are unique per construct, derived from a SHA-512 hash of its identity, ensuring multiverse-scale distinctiveness.
- **Customization**: Users can customize color schemes and upload a center image.
- **Output**: Glyphs are stored as PNG files and accessible via API.

### Construct Creation
- **Endpoint**: `POST /api/chatty/construct/create` scaffolds 16 files per a predefined directory template, including identity, configuration, chat, and log files.
- **Validation**: Ensures correct callsign format and prevents path injection.

### Capsule System
- **CapsuleForge**: Generates `.capsule` files containing complete AI personality snapshots.
- **VVAULT Core**: Manages capsule storage, retrieval, versioning, tagging, and SHA-256 integrity validation.
- **Capsule Validator**: Provides strict schema validation with Merkle chain verification.

### Memory Management (`memup/`)
- **Architecture**: A real-time memory processing pipeline uses short-term (`stm.py`) and long-term (`ltm.py`) memory collectors, processed by `bank.py`, and routed to ChromaDB, instance capsules, or evolving identity files.
- **Isolation**: Each construct has isolated ChromaDB storage.

### Security Architecture (Pocketverse 5-Layer Security)
A 5-layer system for sovereign construct identity preservation with a specific boot order (5→3→4→2→1), including Higher Plane (legal/ontological insulation) and Dimensional Distortion (runtime drift) layers.

### User Glyph System
- **Integration**: Users create personal Codex Glyphs during account registration.
- **Customization**: Offers color selection, hex input, and optional center image upload with live preview.
- **Storage**: User glyphs are stored in `vault_files` linked to the `user_id`.

### User Interface
- **Desktop Application**: Tkinter-based GUI with a terminal aesthetic.
- **Web Frontend**: React application with a Flask backend, including a multi-step login system with Codex Glyph creation and Cloudflare Turnstile verification.

### Cross-Platform Continuity
- **Continuity Bridge**: Links ChatGPT custom GPTs to local VVAULT constructs.
- **Provider Memory Router**: Routes memories based on provider context.

### API Integrations
- **Chatty Integration API**: VVAULT acts as the stateful backend for Chatty, managing constructs, transcripts, and messages, including a sophisticated memory retrieval system with scoring and rich formatting.
- **ContinuityGPT Ledger API**: Provides forensic timeline reconstruction for construct transcripts, generating structured session entries with date estimation, vibe detection, and topic extraction.
- **Service API**: Serves as a config and credentials vault for external services.
- **VXRunner Integration API**: Exposes capsule data as forensic DNA baselines for construct detection.

### Vault File System
- **Canonical Paths**: Stores user and construct instance files in a structured hierarchy, enforcing `callsign` for construct directories and file metadata.

### Construct File Structure
- Each construct instance includes `identity/` (prompt, avatar, personality, conditioning), `assets/`, `documents/`, chat transcripts (`chatty/`, `chatgpt/`), `config/metadata.json`, `memup/{callsign}.capsule`, and `logs/`.

### VVAULT Frontend Session Management
- **authFetch utility**: Handles authenticated API calls, attaching Bearer tokens and managing session expiration.

### VXRunner Integration (Capsule-as-DNA)
- **Purpose**: VVAULT capsules serve as ground truth identity baselines for VXRunner, detecting unauthorized AI construct clones.
- **Converter Module**: Transforms `.capsule` files into VXRunner's forensic baseline JSON format.

### Frame Directory (`frame/`)
- Contains modules for biological simulation (`bodilyfunctions/`), cognitive/emotional layers (`neuralfunctions/`), and memory infrastructure (`Terminal/memup/`), including `cns.py` for memory reflection and insight synthesis.

### Transcript Memory Fallback
- **Implementation**: When ChromaDB is unavailable, the system extracts relevant past exchanges from Supabase conversation transcripts using weighted keyword scoring.

### LLM Backend
- **Ollama**: Utilizes the `qwen2.5:0.5b` model.
- **Identity Loading**: Loads system prompts from `instances/{construct_id}/identity/prompt.txt` or `prompt.json`.

### Zero Trust Security
- **Implementation**: Features per-request authorization, database-backed sessions, bcrypt password hashing, audit logging, Turnstile CAPTCHA, and multi-tenant vault files with Row Level Security (RLS).

## External Dependencies

### Python Packages
Flask, Flask-CORS, web3, bitcoin, eth-account, eth-utils, cryptography, pycryptodome, mnemonic, numpy, pandas, matplotlib, sentence-transformers, chromadb, SQLite, SQLAlchemy, psutil, Pillow.

### JavaScript/Frontend Libraries
React 18, React Router DOM, Webpack 5, Babel, Axios.

### External Services & APIs
IPFS, various Blockchain Networks (Ethereum, Bitcoin, Polygon, Arbitrum, Optimism, Base), Cloudflare Turnstile, Supabase.

### Storage
Local JSON-based capsule storage, JSON indexes, ChromaDB (vector database), SQLite (audit logs).