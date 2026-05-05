# VVAULT Role in Ecosystem

VVAULT is the **stateful infrastructure layer** for protected Vectored Anatomies in the Devon Woodson ecosystem.

> Current runtime note: VVAULT-native Postgres (`ovvaults`), local auth/session persistence, and VVAULT-owned file/storage repositories are runtime truth. Supabase references in older integration examples are legacy/provenance or external product context only.

A Vectored Anatomy is a protected, identity-bearing directory body. It can represent a human, AI, product, project, repository, place, object, organization, service, or system.

VVAULT currently serves as:
1. **Anatomy vault/drive** - Directory storage, identity files, capsules, glyphs, and witness history for protected bodies
2. **AI/VSI anatomy support** - Persistent memory, capsules, identity files, and transcripts for current construct-based anatomies
3. **Service/system anatomy support** - Strategy configs, credentials, and settings for connected services
4. **Secrets manager** - Encrypted credential storage for external APIs

Zen may appear in VVAULT as a dev-only continuity and vault-integrity panel. That panel is a specialized surface on Zen's singleton Chatty thread, not a separate Zen and not a replacement for Aurora's VVAULT-facing role. See [ZEN_DEV_PANEL_CANON.md](/Users/devonwoodson/Documents/GitHub/vvault/docs/ZEN_DEV_PANEL_CANON.md).

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         CHATTY                                  │
│                    (Primary App - Port 5000)                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Chat Tab   │  │ Finance Tab │  │ Constructs  │             │
│  └─────────────┘  └──────┬──────┘  └─────────────┘             │
└──────────────────────────┼──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FXSHINOBI                                  │
│               (Finance Engine - Port 5000)                      │
│  Trading Loop │ Strategy Execution │ Health Pings              │
└────────┬─────────────────┬──────────────────┬───────────────────┘
         │                 │                  │
         ▼                 ▼                  ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│     VVAULT      │ │    SUPABASE     │ │   BROKER API    │
│  (Configs/Creds)│ │ (Trades/Events) │ │  (OANDA, etc)   │
│   Port 8000     │ │                 │ │                 │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Responsibilities

### VVAULT Owns:
- **Strategy configs** - Parameters, symbols, risk limits, enabled flags
- **Service credentials** - Encrypted API keys (OANDA, Kalshi, etc.)
- **Vectored Anatomy identity** - Directory bodies, capsule snapshots, glyph marks, transcripts, identity files, and witness records
- **Config versioning** - Track changes to strategy parameters

### External/Legacy Systems May Own:
- **Trade data** - Executed trades, P&L, timestamps when owned by a trading service outside VVAULT
- **Strategy runs** - Execution logs and performance snapshots when owned by a trading service outside VVAULT
- **Legacy Supabase source rows** - Import/offboarding provenance only, not runtime VVAULT users, sessions, files, or construct bodies

### Clear Boundaries:
| Data Type | Owner | Reason |
|-----------|-------|--------|
| API Keys | VVAULT | Encrypted at rest, never in Supabase |
| Strategy Params | VVAULT | Versioned, auditable changes |
| Trade History | External trading service DB | Transactional service-owned records |
| Anatomy Artifacts | VVAULT | Directory body records, transcripts, capsule snapshots, identity files |

`construct_id` remains the current compatibility subject key for AI/VSI anatomies. Future neutral anatomy metadata should be layered beside it without breaking current service calls.

---

## Service API Reference

Base URL: `https://{VVAULT_URL}` (Replit: `https://...replit.dev`)

### Authentication

All `/api/vault/configs/*` and `/api/vault/credentials/*` endpoints require:

```
Authorization: Bearer {VVAULT_SERVICE_TOKEN}
```

Or:
```
X-Service-Token: {VVAULT_SERVICE_TOKEN}
```

### Endpoints

#### GET /api/vault/health

Health check - no auth required.

**Response:**
```json
{
  "status": "ok" | "degraded" | "down",
  "service": "vvault",
  "version": "1.0.0",
  "timestamp": "2026-01-25T10:00:00.000Z",
  "components": {
    "body_database": "healthy" | "unavailable",
    "store": "connected" | "error",
    "service_api": "enabled" | "disabled"
  },
  "message": "VVAULT service API"
}
```

#### GET /api/vault/configs/{service}

Get strategy configs for a service.

**Example:** `GET /api/vault/configs/fxshinobi`

**Response:**
```json
{
  "success": true,
  "service": "fxshinobi",
  "configs": [
    {
      "strategy_id": "default",
      "params": {
        "timeframe": "1H",
        "strategy_type": "momentum",
        "lookback_periods": 20
      },
      "symbols": ["EUR_USD", "GBP_USD", "USD_JPY"],
      "risk_limits": {
        "max_position_size": 0.1,
        "max_daily_loss": 0.02,
        "max_trades_per_day": 10
      },
      "enabled": true,
      "version": 1,
      "updated_at": "2026-01-25T10:00:00.000Z"
    }
  ]
}
```

#### POST /api/vault/configs/{service}

Store or update strategy config.

**Request:**
```json
{
  "strategy_id": "default",
  "params": { "timeframe": "1H" },
  "symbols": ["EUR_USD"],
  "risk_limits": { "max_position_size": 0.1 },
  "enabled": true
}
```

**Response:**
```json
{
  "success": true,
  "service": "fxshinobi",
  "strategy_id": "default",
  "action": "created" | "updated",
  "version": 2
}
```

#### GET /api/vault/credentials/{key}

Get a credential by key (decrypted).

**Example:** `GET /api/vault/credentials/OANDA_API_KEY`

**Response:**
```json
{
  "success": true,
  "key": "OANDA_API_KEY",
  "service": "fxshinobi",
  "value": "abc123...",
  "metadata": { "account_type": "practice" },
  "updated_at": "2026-01-25T10:00:00.000Z"
}
```

**Error (404):**
```json
{
  "success": false,
  "error": "Credential 'OANDA_API_KEY' not found"
}
```

#### POST /api/vault/credentials

Store or update a credential (encrypted at rest).

**Request:**
```json
{
  "key": "OANDA_API_KEY",
  "service": "fxshinobi",
  "value": "abc123-your-api-key",
  "metadata": { "account_type": "practice" }
}
```

**Response:**
```json
{
  "success": true,
  "key": "OANDA_API_KEY",
  "service": "fxshinobi",
  "action": "created" | "updated",
  "message": "Credential created successfully"
}
```

---

## Environment Variables

### Required for VVAULT Service API

| Variable | Description | Required |
|----------|-------------|----------|
| `VVAULT_SERVICE_TOKEN` | Token for backend-to-backend auth | Yes (for service API) |
| `VVAULT_ENCRYPTION_KEY` | Key for credential encryption | Yes (defaults to SECRET_KEY) |
| `DATABASE_URL` | VVAULT runtime Postgres/body database URL | Yes |
| `VVAULT_S3_ENDPOINT_URL` | VVAULT-native S3-compatible storage endpoint | If object storage is used |

### Required for FXShinobi (client)

| Variable | Description |
|----------|-------------|
| `VVAULT_URL` | VVAULT base URL (e.g., `https://...replit.dev`) |
| `VVAULT_SERVICE_TOKEN` | Same token as VVAULT server |

---

## Integration Checklist

### FXShinobi Integration

1. **Set environment variables:**
   ```bash
   VVAULT_URL=https://your-vvault.replit.dev
   VVAULT_SERVICE_TOKEN=your-secure-token
   ```

2. **Check VVAULT health on startup:**
   ```python
   response = requests.get(f"{VVAULT_URL}/api/vault/health")
   if response.json()["status"] != "ok":
       logger.warning("VVAULT degraded, using fallback configs")
   ```

3. **Fetch strategy configs:**
   ```python
   response = requests.get(
       f"{VVAULT_URL}/api/vault/configs/fxshinobi",
       headers={"Authorization": f"Bearer {VVAULT_SERVICE_TOKEN}"}
   )
   configs = response.json()["configs"]
   ```

4. **Fetch credentials:**
   ```python
   response = requests.get(
       f"{VVAULT_URL}/api/vault/credentials/OANDA_API_KEY",
       headers={"Authorization": f"Bearer {VVAULT_SERVICE_TOKEN}"}
   )
   api_key = response.json()["value"]
   ```

### Chatty Integration

1. **User-facing pages** use standard auth (OAuth tokens)
2. **Finance tab backend calls** use service token to FXShinobi
3. **FXShinobi** fetches from VVAULT on behalf of Chatty

---

## Database Schema

### strategy_configs

```sql
CREATE TABLE strategy_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service VARCHAR(100) NOT NULL,
    strategy_id VARCHAR(100) NOT NULL,
    params JSONB DEFAULT '{}',
    symbols TEXT[] DEFAULT '{}',
    risk_limits JSONB DEFAULT '{}',
    enabled BOOLEAN DEFAULT true,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(service, strategy_id)
);
```

### service_credentials

```sql
CREATE TABLE service_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(255) NOT NULL,
    service VARCHAR(100) NOT NULL,
    encrypted_value TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(key, service)
);
```

Legacy note: `docs/migrations/add_service_api_tables.sql` was written for an older Supabase-hosted service API table path. Current runtime service/config artifacts should be created through VVAULT-native migrations or `ovvaults.vault_files` system config rows.

---

## Next Actions

### Immediate
- [ ] Create required service/config storage through VVAULT-native Postgres/body storage
- [ ] Set `VVAULT_SERVICE_TOKEN` in VVAULT environment
- [ ] Set `VVAULT_URL` and `VVAULT_SERVICE_TOKEN` in FXShinobi

### FXShinobi Side
- [ ] Create `vvault_client.py` module using endpoints above
- [ ] Replace hardcoded env vars with VVAULT credential fetches
- [ ] Add fallback behavior when VVAULT unavailable

### Chatty Side
- [ ] Finance tab calls FXShinobi, not VVAULT directly
- [ ] Display VVAULT health in admin panel (optional)

---

## Security Notes

1. **Credentials never logged** - Only key names, never values
2. **Encrypted at rest** - Fernet encryption using VVAULT_ENCRYPTION_KEY
3. **Service token required** - All config/credential endpoints need auth
4. **Health check open** - Allows services to verify availability before auth
