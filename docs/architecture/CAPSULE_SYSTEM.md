# VVAULT Capsule System

## Last Updated: 2026-02-11

## Overview

Capsules are versioned personality snapshots that capture a complete picture of an AI construct's identity at a point in time. They include MBTI profiles, Big Five traits, cognitive biases, memory categorization, emotional baselines, and behavioral patterns.

Capsules go in `memup/` because they directly control construct memory, not identity.

## Storage Location

```
instances/{callsign}/memup/{callsign}.capsule
```

In Supabase (`vault_files` table):
- **filename**: `{callsign}.capsule`
- **storage_path**: `instances/{callsign}/memup/{callsign}.capsule`
- **construct_id**: `{callsign}`
- **type**: `capsule`

## Capsule Lifecycle

### 1. Generation (CapsuleForge)

**Source**: `scripts/capsules/capsuleforge.py`

CapsuleForge generates `.capsule` files containing:

```json
{
  "metadata": {
    "instance_name": "katana-001",
    "uuid": "unique-capsule-id",
    "timestamp": "ISO-8601",
    "fingerprint_hash": "sha256-hex-digest",
    "tether_signature": "DEVON-ALLEN-WOODSON-SIG",
    "capsule_version": "1.0.0",
    "generator": "CapsuleForge",
    "vault_source": "VVAULT"
  },
  "traits": { "trait_name": 0.85, ... },
  "personality": {
    "personality_type": "INFJ",
    "mbti_breakdown": { "E_I": 0.3, "S_N": 0.8, ... },
    "big_five_traits": { "openness": 0.8, "conscientiousness": 0.9, ... },
    "emotional_baseline": { "joy": 0.6, "calm": 0.7, ... },
    "cognitive_biases": ["confirmation_bias", ...],
    "communication_style": { ... }
  },
  "memory": {
    "short_term_memories": [...],
    "long_term_memories": [...],
    "emotional_memories": [...],
    "procedural_memories": [...],
    "episodic_memories": [...],
    "memory_count": 0,
    "last_memory_timestamp": "ISO-8601"
  },
  "environment": {
    "system_info": { ... },
    "runtime_environment": { ... },
    "active_processes": [...],
    "network_connections": [...],
    "hardware_fingerprint": { ... }
  },
  "additional_data": {
    "identity": { ... },
    "tether": { ... },
    "sigil": { ... },
    "continuity": { ... },
    "covenantInstruction": "",
    "bootstrapScript": "",
    "resurrectionTriggerPhrase": ""
  }
}
```

### 2. Storage (VVAULTCore)

**Source**: `vvault/memory/vvault_core.py`

VVAULTCore manages **local** capsule storage under `vvault/memory/capsules/{instance_name}/`:

- **Automatic versioning**: Capsules are never overwritten. `store_capsule()` creates new timestamped versions.
- **SHA-256 fingerprinting**: Every capsule gets a cryptographic hash via `fingerprint_hash` in metadata.
- **Index management**: Per-instance indexes (stored in `vvault/memory/indexes/`) track all capsule versions with metadata, including tags.

Key methods:
- `store_capsule(capsule_data)` — Store a new capsule version (extracts instance_name from capsule metadata)
- `retrieve_capsule(instance_name, uuid)` — Retrieve specific version
- `get_latest_capsule(instance_name)` — Get most recent capsule
- `verify_integrity(instance_name, uuid)` — SHA-256 verification

**Note**: VVAULTCore stores capsules locally on disk. Supabase `vault_files` stores capsules separately at `instances/{callsign}/memup/{callsign}.capsule`. These are two parallel storage paths — local (VVAULTCore) and remote (Supabase).

### 3. Validation (CapsuleValidator)

**Source**: `scripts/capsules/capsule_validator.py`

Provides schema validation for capsule integrity:
- Schema completeness checks
- Fingerprint hash verification

### 4. Supabase Integration (via VVAULT Web Server)

On construct creation (`POST /api/chatty/construct/create` in `vvault/server/vvault_web_server.py`), an empty capsule scaffold is stored:
- Uploaded to Supabase `vault_files` with proper `storage_path`
- `construct_id` set to the callsign
- Ready for CapsuleForge to populate with actual personality data

## User Capsules

**Source**: `vvault/memory/user_capsule_forge.py`

Users are also treated as "living capsules" — versioned, evolving entities:

- **UserCapsuleForge** generates user capsules capturing preferences, interaction patterns, and relationships with constructs
- Tracks `UserInteraction`, `UserPreference`, and `ConstructRelationship` data
- Integrates with QuantumIdentityEngine for identity signatures

## VXRunner Integration

**Source**: `vvault/server/vxrunner_baseline.py`

Capsules serve as forensic DNA baselines for VXRunner (construct clone detection):
- Converts `.capsule` files into VXRunner forensic baseline JSON
- Extracts lexical, structural, tonal, and signature phrase features
- Used to detect unauthorized clones or derivatives

## Current State (7 Constructs)

| Construct    | Capsule Location                                |
|-------------|------------------------------------------------|
| aurora-001  | `instances/aurora-001/memup/aurora-001.capsule` |
| katana-001  | `instances/katana-001/memup/katana-001.capsule` |
| lin-001     | `instances/lin-001/memup/lin-001.capsule`       |
| monday-001  | `instances/monday-001/memup/monday-001.capsule` |
| nova-001    | `instances/nova-001/memup/nova-001.capsule`     |
| sera-001    | `instances/sera-001/memup/sera-001.capsule`     |
| zen-001     | `instances/zen-001/memup/zen-001.capsule`       |
