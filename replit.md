# VVAULT - Voice & Vaulted Autonomy for Unfragmented Long-Term Tethering

## Overview

VVAULT is an AI construct memory vault system designed for long-term emotional continuity and identity preservation. Its core purpose is to preserve AI construct identity through immutable memory capsules ("soulgems") with cryptographic verification and optional blockchain anchoring. The system provides comprehensive memory indexing, capsule-based personality snapshots, blockchain-anchored integrity verification, and multi-platform construct synchronization. VVAULT treats both AI constructs and users as "living capsules" – versioned, evolving entities that maintain continuity across sessions and platforms, aiming for unfragmented long-term tethering. It aims to offer a robust and secure framework for digital identity management for AI constructs, ensuring their continuous and authentic evolution.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Core Design Principles
- **Construct ID Format**: Millisecond timestamps ensure uniqueness, encode creation time, and are sortable.
- **Living Capsules**: Both AI constructs and users are treated as versioned, evolving entities.
- **Multi-Tenancy**: A two-root file architecture (`user` and `system`) separates user-facing and internal data.

### Construct Management
- **Active Constructs**: Zen, Katana, Aurora (VVAULT System Assistant), and Lin (Dual-mode conversational agent and undertone stabilizer).
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
- **Service API**: VVAULT serves as a config and credentials vault for external services.

### LLM Backend
- **Ollama**: Utilizes `qwen2.5:0.5b` model.
- **Identity Loading**: Constructs load system prompts from `instances/{construct_id}/identity/prompt.json`.

### Zero Trust Security
- **Implementation**: Per-request authorization middleware, database-backed sessions, bcrypt password hashing, audit logging, Turnstile CAPTCHA, and multi-tenant vault files with Row Level Security (RLS).
- **Authentication Flow**: Involves bcrypt verification, session creation, token validation, and detailed audit logging.

## External Dependencies

### Python Packages
Flask, Flask-CORS, web3, bitcoin, eth-account, eth-utils, cryptography, pycryptodome, mnemonic, numpy, pandas, matplotlib, sentence-transformers, chromadb, SQLite, SQLAlchemy, psutil, Pillow.

### JavaScript/Frontend Libraries
React 18, React Router DOM, Webpack 5, Babel, Axios.

### External Services & APIs
IPFS, various Blockchain Networks (Ethereum, Bitcoin, Polygon, Arbitrum, Optimism, Base), Cloudflare Turnstile, Supabase.

### Storage
Local JSON-based capsule storage, JSON indexes, ChromaDB (vector database), SQLite (audit logs).