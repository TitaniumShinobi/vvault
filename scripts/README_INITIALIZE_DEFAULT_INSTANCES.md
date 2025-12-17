# Initialize Default Instances

## Overview

This script automatically creates `zen-001` and `lin-001` instances with complete identity files for new user accounts. It ensures every new user gets properly configured default constructs with:

- **prompt.txt** - System prompt (ignition)
- **conditioning.txt** - Identity enforcement rules
- **{construct}.capsule** - Personality snapshot generated via CapsuleForge

## What Gets Created

### zen-001 (Primary Construct)
- **Location**: `users/{shard}/{user_id}/instances/zen-001/`
- **Identity Files**:
  - `identity/prompt.txt` - Zen's multi-model synthesis identity
  - `identity/conditioning.txt` - Identity enforcement rules
  - `identity/zen-001.capsule` - Generated capsule with traits and personality

### lin-001 (GPT Creator Assistant)
- **Location**: `users/{shard}/{user_id}/instances/lin-001/`
- **Identity Files**:
  - `identity/prompt.txt` - Lin's continuity guardian identity
  - `identity/conditioning.txt` - Identity enforcement rules
  - `identity/lin-001.capsule` - Generated capsule with traits and personality

## Automatic Integration

The script is **automatically called** when a new VVAULT user profile is created via `resolveVVAULTUserId()` in `chatty/vvaultConnector/writeTranscript 3.js`.

**Flow**:
1. User signs up/logs in to Chatty
2. `resolveVVAULTUserId()` is called with `autoCreate=true`
3. If user doesn't exist, `createVVAULTUserProfile()` creates the profile
4. `initializeDefaultInstances()` is automatically called
5. zen-001 and lin-001 instances are created with all identity files

## Manual Usage

You can also run the script manually:

```bash
python3 vvault/scripts/initialize_default_instances.py <vvault_user_id> [vault_path]
```

**Example**:
```bash
python3 vvault/scripts/initialize_default_instances.py devon_woodson_1762969514958
```

## Requirements

- Python 3.6+
- CapsuleForge module (from `vvault/capsuleforge.py`)
- VVAULT directory structure

## Error Handling

- If initialization fails, it logs a warning but **does not fail user creation**
- User profile is still created successfully
- Instances can be initialized later manually if needed

## Identity File Templates

The script uses canonical identity templates:

- **Zen**: Multi-model synthesis construct (DeepSeek + Phi3 + Mistral)
- **Lin**: Continuity guardian construct (infrastructure-born orchestrator)

Both templates are embedded in the script and match the official identity specifications.

