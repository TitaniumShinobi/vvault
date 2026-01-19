# VVAULT Supabase Schema: Current Overview & Future Options

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

## Current Status
- Working secure relational schema with memory and logging.
- Ready to utilize Supabase client APIs for constructing and fetching constructs.
- Ability to store per-construct memory and log state history.

## Optional Next Steps
- Implement Supabase Edge Function for automatic state_snapshot logging.
- Enhance with additional indexes for UX improvements.
- Develop auth-connected front-end leveraging existing RLS.
- Introduce org/role-based access for scaling to multi-user scenarios.