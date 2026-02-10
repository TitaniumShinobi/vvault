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
- **Chatty Integration API**: VVAULT acts as the stateful backend for Chatty, providing endpoints for managing constructs, transcripts, and messages. Uses `require_chatty_auth` decorator with three auth modes:
  - **API Key mode**: `X-Chatty-Key` header matching `CHATTY_API_KEY` env var + `X-Chatty-User` header (email) for user context
  - **Session mode**: Standard Bearer session token (same as VVAULT web UI login)
  - **Dev mode**: When `CHATTY_API_KEY` env var is not set, endpoints are open; `X-Chatty-User` header is optional for user context
- **Service API**: VVAULT serves as a config and credentials vault for external services.
- **VXRunner Integration API**: VVAULT exposes capsule data as forensic DNA baselines for VXRunner's construct detection system.

### VXRunner Integration (Capsule-as-DNA)
- **Purpose**: VVAULT capsules serve as ground truth identity baselines for VXRunner (NovaRunner), a signal detection framework that detects unauthorized clones, derivatives, and replicants of AI constructs.
- **Converter Module**: `vvault/server/vxrunner_baseline.py` transforms `.capsule` files into VXRunner's forensic baseline JSON format.
- **Feature Extraction**: The converter extracts four feature categories from capsule data:
  - **Lexical Features**: Word frequency (top 50), vocabulary richness, bigram frequency (top 30), avg words per message — extracted from raw memory text
  - **Structural Features**: Avg message length, ellipsis/emoji/asterisk usage ratios, lowercase start ratio, all-caps frequency, question/exclamation ratios
  - **Tonal Features**: Affectionate/playful/defensive/caring/assertive marker ratios, tone distribution, dominant tone — synthesized from personality traits + memory text analysis
  - **Signature Phrases**: Recurring 3-6 word phrases appearing 2+ times, filtered for content relevance
- **API Endpoints**:
  - `GET /api/vxrunner/capsules` — Discovery endpoint listing available capsules with baseline URLs
  - `GET /api/capsules/<name>/vxrunner-baseline` — Fetch forensic baseline for a specific capsule
  - Both endpoints use `VXRUNNER_API_KEY` auth via `X-VXRunner-Key` header or `?key=` query param (open in dev mode when env var not set)
  - `?include_raw_text=false` on baseline endpoint redacts raw memory content
- **Capsule Storage**: `.capsule` files stored in `vvault/server/capsules/` directory
- **Active Capsules**: `nova-001.capsule` (INFJ, primary construct, 30 memory entries, 28 traits)
- **Capsule Generator**: `scripts/capsules/generate_nova_capsule.py` creates authenticated NOVA-001 capsule with real construct data

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

## Agent Instructions

### SUPABASE IS READ-ONLY FOR ALL AGENTS — NO EXCEPTIONS

**This is the highest-priority rule in this repository.**

Agents (Replit, Cursor, Copilot, or any other AI assistant) are **absolutely forbidden** from performing any write, update, or delete operations on the Supabase database. This includes but is not limited to:

- **INSERT** — Do not create new rows in any Supabase table
- **UPDATE** — Do not modify any existing data in any Supabase table
- **DELETE** — Do not remove any rows from any Supabase table
- **ALTER** — Do not change table schemas, columns, or constraints
- **RPC / function calls** that mutate data
- **Storage bucket operations** that upload, overwrite, or delete files
- **Running migration scripts** that touch Supabase

**Read-only queries (SELECT) are permitted** for diagnostic purposes only.

If a data fix is needed in Supabase, the agent must:
1. Describe the problem clearly to the user
2. Provide the exact SQL or Python code the user can run themselves
3. Wait for the user to execute it manually
4. Never execute it on the user's behalf

**Why**: Past agents have created duplicate records, used fabricated paths, corrupted filenames, and created orphan data by writing to Supabase without understanding the data model. The owner manages all Supabase data manually.

### Other Rules

**DO NOT:**
- Modify vault_files data under any circumstances
- Run destructive SQL (DROP, DELETE) without explicit user approval
- Change user emails without verifying against OAuth provider
- Create duplicate user records

**ALWAYS:**
- Query Supabase as read-only to verify data issues (not just check code)
- Check logs for "Created new OAuth user" which indicates email mismatch
- Provide SQL/code to the user for them to run if a fix is needed
- Log all proposed database modifications with before/after values
- Update replit.md with any new troubleshooting discoveries

## Supabase Configuration

### Key Tables
- `users` — User accounts (id, email, name)
- `vault_files` — All user files with storage_path for folder hierarchy
- `strategy_configs` — Service configurations
- `service_credentials` — Encrypted API credentials

### Storage Path Format
All vault files use this path structure in the `storage_path` column:
```
{user_id}/{user_slug}/{folder_structure}/{filename}
```
The frontend strips the UUID and user slug prefixes to display clean folder paths.

### How to Find User Data (READ-ONLY)
```python
result = supabase.table('users').select('id, email, name').eq('email', 'user@email.com').execute()
files = supabase.table('vault_files').select('id', count='exact').eq('user_id', user_id).execute()
```

## Troubleshooting Guide

### "This folder is empty" in Vault UI
**Common Causes:**
1. **Email mismatch in users table** — OAuth returns exact email from Google; if users table has a typo, a new empty user is created
2. **NULL storage_path values** — Files exist but have NULL storage_path, breaking folder hierarchy
3. **Wrong user_id in vault_files** — Files associated with different user ID than logged-in user

### OAuth Creates New User Instead of Finding Existing
**Root Cause:** Email in users table doesn't match OAuth provider's email exactly.

### Files Not Appearing in Correct Folders
**Root Cause:** storage_path column is NULL or incorrectly formatted.
**Storage Path Patterns:**
- ChatGPT transcripts: `instances/{construct_id}/chatgpt/{filename}`
- Chatty transcripts: `instances/{construct_id}/chatty/chat_with_{construct_id}.md`
- Identity files: `instances/{construct_id}/identity/{filename}`
- Account files: `account/{filename}`
- Documents: `library/documents/{filename}`
- Media: `library/media/{filename}`

## Production Deployment

### Server Infrastructure
- **OS**: Ubuntu 24.04 (DigitalOcean Droplet)
- **Public IP**: `165.245.136.194`
- **Domain**: `vvault.thewreck.org`
- **App Path**: `/opt/vvault`
- **Virtualenv**: `/opt/vvault/venv`

### DNS Configuration
- **Provider**: Cloudflare
- **Record Type**: A record for `vvault.thewreck.org` → `165.245.136.194`
- **Proxy Status**: DNS only (grey cloud)

### Backend Startup (Gunicorn)
```bash
source /opt/vvault/venv/bin/activate
gunicorn --bind 127.0.0.1:8000 vvault.server.vvault_web_server:app
```
Required: `pip install oauthlib requests-oauthlib`

### Nginx Reverse Proxy
Config: `/etc/nginx/sites-available/vvault`
```nginx
server {
    listen 80;
    server_name vvault.thewreck.org;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### API Endpoints
- `GET /` — Root status check: `{"status": "ok", "service": "vvault-api"}`
- `GET /api/health` — Detailed health check with timestamp and version
- `GET /api/vault/health` — Vault-specific health check

### HTTPS/SSL Plan (Let's Encrypt)
```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d vvault.thewreck.org
```
Do not manually force HTTPS until certbot has been run.