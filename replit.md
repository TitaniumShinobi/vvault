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

## Critical User Data

### Primary User Account
- **User ID**: `7e34f6b8-e33a-48b5-8ddb-95b94d18e296`
- **Email**: `dwoodson92@gmail.com`
- **Display Name**: Devon Woodson
- **User Slug**: `devon_woodson_1762969514958`

### Supabase Configuration
- **Project URL**: `https://xkxckpbexjuhmfdjsopd.supabase.co`
- **Key Tables**:
  - `users` - User accounts (id, email, name)
  - `vault_files` - All user files with storage_path for folder hierarchy
  - `strategy_configs` - Service configurations
  - `service_credentials` - Encrypted API credentials

### Storage Path Format
All vault files use this path structure in the `storage_path` column:
```
{user_id}/{user_slug}/{folder_structure}/{filename}
```
Example: `7e34f6b8-e33a-48b5-8ddb-95b94d18e296/devon_woodson_1762969514958/instances/katana-001/chatgpt/test.md`

The frontend strips the UUID and user slug prefixes to display clean folder paths like `instances/katana-001/chatgpt/`.

## Troubleshooting Guide

### "This folder is empty" in Vault UI

**Symptom**: User logs in but sees empty vault with "This folder is empty" message.

**Common Causes**:

1. **Email mismatch in users table**
   - OAuth returns the exact email from Google (e.g., `dwoodson92@gmail.com`)
   - If the `users` table has a typo or different email, a new empty user is created
   - **Fix**: Update the email in Supabase `users` table to match exactly:
     ```python
     supabase.table('users').update({'email': 'correct@email.com'}).eq('id', 'user-uuid').execute()
     ```

2. **NULL storage_path values**
   - Files exist but have NULL `storage_path`, breaking folder hierarchy
   - **Fix**: Run storage_path inference based on construct_id and filename patterns
   - See `scripts/migrate_to_supabase.py` for inference logic

3. **Wrong user_id in vault_files**
   - Files associated with different user ID than currently logged in user
   - **Diagnosis**: Query vault_files for expected user_id
   - **Fix**: Update user_id if records were created under wrong account

### OAuth Creates New User Instead of Finding Existing

**Root Cause**: Email in `users` table doesn't match OAuth provider's email exactly.

**Prevention**:
- Always verify email matches exactly (case-sensitive in some providers)
- Log the email lookup at OAuth callback for debugging
- Never manually edit user emails without verifying against OAuth provider

### Files Not Appearing in Correct Folders

**Root Cause**: `storage_path` column is NULL or incorrectly formatted.

**Storage Path Patterns**:
- ChatGPT transcripts: `instances/{construct_id}/chatgpt/{filename}`
- Chatty transcripts: `instances/{construct_id}/chatty/chat_with_{construct_id}.md`
- Identity files: `instances/{construct_id}/identity/{filename}`
- Account files: `account/{filename}`
- Documents: `library/documents/{filename}`
- Media: `library/media/{filename}`

**Fix Script**:
```python
# Infer storage_path based on construct_id and filename patterns
if construct_id in ['katana', 'katana-001']:
    if filename.endswith('-K1.md'):
        storage_path = f'{base_path}instances/katana-001/chatgpt/{filename}'
    elif filename in ['conditioning.txt', 'prompt.txt']:
        storage_path = f'{base_path}instances/katana-001/identity/{filename}'
```

## Agent Instructions

### DO NOT:
- Modify vault_files data without user approval (except NULL storage_path fixes)
- Run destructive SQL (DROP, DELETE, UPDATE on production data)
- Change the primary user's email without verifying against OAuth provider
- Create duplicate user records

### ALWAYS:
- Query Supabase directly to verify data issues (not just check code)
- Check logs for "Created new OAuth user" which indicates email mismatch
- Preserve existing user_id associations when fixing storage_path
- Update replit.md with any new troubleshooting discoveries