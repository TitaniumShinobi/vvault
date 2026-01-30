# VVAULT Role in Ecosystem

VVAULT is the **stateful infrastructure layer** for the Devon Woodson AI ecosystem, serving as:
1. **Construct identity vault** - Persistent memory, capsules, and identity files for VSIs
2. **Service config store** - Strategy configs, credentials, and settings for connected services
3. **Secrets manager** - Encrypted credential storage for external APIs

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
- **Construct identity** - VSI capsules, transcripts, identity files
- **Config versioning** - Track changes to strategy parameters

### Supabase Owns:
- **Trade data** - Executed trades, P&L, timestamps
- **User sessions** - Auth tokens, user profiles
- **Strategy runs** - Execution logs, performance snapshots
- **Vault files** - Construct transcripts, documents

### Clear Boundaries:
| Data Type | Owner | Reason |
|-----------|-------|--------|
| API Keys | VVAULT | Encrypted at rest, never in Supabase |
| Strategy Params | VVAULT | Versioned, auditable changes |
| Trade History | Supabase | Transactional, needs queries |
| Construct Memory | VVAULT+Supabase | Transcripts in Supabase, capsules synthesized |

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
    "supabase": "connected" | "not_configured",
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
| `SUPABASE_URL` | Supabase project URL | Yes |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key | Yes |

### Required for FXShinobi (client)

| Variable | Description |
|----------|-------------|
| `VVAULT_URL` | VVAULT base URL (e.g., `https://...replit.dev`) |
| `VVAULT_SERVICE_TOKEN` | Same token as VVAULT server |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon or service key |

---

## Integration Checklist

### FXShinobi Integration

1. **Set environment variables:**
   ```bash
   VVAULT_URL=https://your-vvault.replit.dev
   VVAULT_SERVICE_TOKEN=your-secure-token
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your-anon-key
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

Run `docs/migrations/add_service_api_tables.sql` in Supabase SQL Editor to create these tables.

---

## Next Actions

### Immediate
- [ ] Run `add_service_api_tables.sql` in Supabase
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
