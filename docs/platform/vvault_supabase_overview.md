# VVAULT Supabase Schema: Legacy Overview

> Historical/offboarding snapshot only. This document predates the VVAULT-native body/auth/file runtime cutover and still references older `vvault_user` / `construct_shard` shapes. For active runtime readiness, use `docs/operations/RUNTIME_CLOSURE_CHECKLIST.md`. For current pass/fail standards, use `docs/rubrics/VVAULT_RUNTIME_CLOSURE_RUBRIC.md`.

## ✅ Core Structure
- **vvault_user table**: Stores user info.
- **construct_shard table**: Main data unit for memory/logical shard.
- **FK from construct_shard.user_id to vvault_user.id**: Enforces user ownership.
- **RLS on construct_shard**: Restricts data access to owners.
- **Seed rows**: Sample shards for development/testing.
- **Index on user_id**: Enhances auth queries & RLS filtering.
- **Migration file**: `vvault_construct_shard_init.sql` for version control.

## 🧠 Memory System
- **memory_fragment table**: Stores vectorized text and metadata per shard.
- **FK to construct_shard.id**: Links memory to shard.
- **Indexes on shard_id, created_at**: Facilitate filtering and display.
- **RLS for shard ownership**: Access limited to owners.
- **Trigger for updated_at**: Tracks memory change timestamps.
- **Migration file**: `vvault_memory_state_migration.sql`.

## 🕓 State Logging
- **state_history table**: Logs construct state snapshots.
- **FK to construct_shard.id**: Tied to constructs.
- **Indexes on shard_id, created_at**: For historical queries.
- **RLS for shard ownership**: Owner-specific access to logs.

## 🛠️ Dev/Maintenance Tooling (Optional)
- **SQL migrations**: Offers version control & portability.
- **Supabase Edge Function**: Potential for automated snapshot triggers (Scaffolded).
- **Seed for vvault_user**: Prepare fake users for testing without Auth.
- **Index on created_at**: Speed up timeline queries (Optional).

## 💻 Client Integration (Optional)
- **insertShard(...)**: Client-side code to create new construct_shard.
- **fetchMyShards(...)**: Retrieve all user-owned constructs.
- **fetchShardsWithFragments(...)**: Obtain constructs with memory data.

---

## Historical Status
- This was a legacy schema direction for Supabase-era development.
- It is not the runtime authority for VVAULT readiness, users, sessions, files, constructs, or memory.
- Supabase references here are retained only as provenance for legacy extraction/offboarding.

## Obsolete Next Steps
- Supabase Edge Functions, RLS-backed runtime access, and Supabase client APIs are not current runtime closure work.
- Use VVAULT-native Postgres tables under `ovvaults`, local auth/session persistence, and VVAULT-owned file/storage repositories instead.
