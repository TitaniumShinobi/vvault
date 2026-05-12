# VVAULT Capsule System

## Last Updated: 2026-04-30

## Current Verdict

A VVAULT capsule is a portable, versioned anatomy snapshot. It should preserve accurate, relevant, non-redundant continuity meaning with source-backed provenance. A capsule is not a transcript dump, folder tree, storage manifest, environment dump, or raw memory archive.

The repository currently contains three different capsule generations/surfaces. They are related, but they are not the same thing.

| Surface | Status | Meaning |
|---|---|---|
| CapsuleForge v1 | Legacy/recovered | Full personality, memory, environment, and signature export. Valuable as provenance, but too broad for the newer precision capsule standard. |
| memup_sync v2 | Implemented/current backend artifact | Transcript-derived sync ledger stored as `.capsule`. Useful for preview and continuity indexing, but not canonical CapsuleForge. |
| CapsuleForge v3 | Proposed/dry-run only | Precision capsule proposal: accurate, relevant, non-redundant, portable, source-backed, storage-topology-free, optional custom body. |

## Capsule Contract

A high-quality capsule should satisfy these rules:

- Accurate: every claim is true or source-backed.
- Relevant: every field helps identity, continuity, routing, recovery, provenance, or runtime interpretation.
- Non-redundant: canon data, transcript bodies, metadata, and source catalogs are not duplicated.
- Portable: capsule content does not depend on local machines, repo folders, or storage layout.
- Source-backed: important claims point to artifact IDs, source names, chronology keys, or hashes.
- Storage-topology-free: storage paths, local paths, bucket internals, and implementation folders are not capsule body data.
- Custom-body tolerant: each construct may have a different body grammar, and `body` may be null.

Allowed source references:

- `artifact_id`
- `source_name`
- `source_type`
- `chronology_key`
- `source_date`
- `sha256`
- concise source-backed support classes such as `identity`, `memory`, `continuity`, `sigil`, or `provenance`

Excluded by default:

- raw transcript paragraphs
- whole conversation bodies
- local file paths such as `/Users/...`
- storage paths such as `instances/{callsign}/...` inside capsule body fields
- `identity_refs` path maps
- `glyph_path`
- duplicated canon documents
- environment dumps, process lists, network lists, or hardware fingerprints unless explicitly justified by a narrow forensic capsule subtype

A transcript file name may appear as a source name or chronology handle. A storage path belongs to the vault index, not the capsule body.

## Storage Surfaces

### VVAULT `ovvaults.vault_files`

The active VVAULT-native capsule path convention is:

```text
instances/{callsign}/memup/{callsign}.capsule
```

The preserve-and-derive materialization convention is:

```text
instances/{callsign}/memup/{callsign}.materialized.capsule
```

A future v3 proposal may recommend:

```text
instances/{callsign}/memup/{callsign}.v3.capsule
```

The path is a vault storage/index concern. It should not be copied into capsule body fields as identity data.

### Local VVAULTCore

`vvault/memory/vvault_core.py` contains older local capsule storage behavior under `vvault/memory/capsules/{instance_name}/`. Treat this as a legacy/local capsule surface unless a current runtime path explicitly uses it.

## Generation Surfaces

### CapsuleForge v1: Legacy Full Export

The legacy CapsuleForge docs describe `.capsule` files with:

- `metadata`
- `traits`
- `personality`
- `memory`
- `environment`
- `additional_data`
- `signatures`

This generation is useful as recovered provenance, but it does not satisfy the newer precision capsule contract by default. It can be too large, too inline-memory-heavy, and too environment/path-oriented for SaaS-scale capsule use.

Known example shape:

```json
{
  "metadata": {
    "capsule_version": "1.0.0",
    "generator": "CapsuleForge"
  },
  "traits": {},
  "personality": {},
  "memory": {},
  "environment": {},
  "additional_data": {},
  "signatures": {}
}
```

### memup_sync v2: Implemented Transcript Sync Ledger

`vvault/server/memup_sync.py` currently builds transcript-derived `.capsule` JSON with:

- `construct_id`
- `capsule_version: 2.0.0`
- `generator: memup_sync`
- `summary`
- `sessions`
- `sync_stats`

This is implemented and useful for previewing synchronized continuity, but it is not CapsuleForge. It may include session filenames, first/last exchange snippets, and parser-selected continuity hooks. Those details make it a sync ledger, not the final precision capsule standard.

### CapsuleForge v3: Proposed Precision Capsule

`vvault/server/capsule_v3_dry_run.py` is a read-only proposal helper. It is not proof that v3 is live or persisted.

The intended v3 direction is:

```json
{
  "metadata": {
    "construct_id": null,
    "capsule_uuid": null,
    "lineage_uuid": null,
    "capsule_version": "3.0.0",
    "profile_kind": "custom",
    "generated_at": null,
    "generator": "CapsuleForge v3",
    "fingerprint_hash": null,
    "tether_signature": null
  },
  "quality_contract": {
    "accurate": true,
    "relevant": true,
    "non_redundant": true,
    "portable": true,
    "source_backed": true,
    "storage_topology_free": true
  },
  "identity": {
    "construct_id": null,
    "role": null,
    "core_definition": null,
    "do_not_flatten_into": []
  },
  "memory": {
    "core_memories": [],
    "continuity_hooks": [],
    "memory_index_refs": []
  },
  "source_manifest": {
    "sources": []
  },
  "retrieval_policy": {
    "primary": "memory_index_refs",
    "fallback": ["source_manifest"],
    "requires_source_hash": true
  },
  "signatures": {
    "linguistic_sigil": {
      "signature_phrase": null,
      "common_phrases": []
    },
    "visual_sigil": {
      "artifact_id": null,
      "glyph_hash": null,
      "number_band_hash": null,
      "render_profile": null,
      "generated_at": null
    }
  },
  "body": null
}
```

`body` is optional. A construct-specific body is allowed only when source-backed, relevant, non-redundant continuity data has no better home in the shared envelope.

## Construct Creation

On construct creation (`POST /api/chatty/construct/create` in `vvault/server/vvault_web_server.py`), VVAULT writes the canonical GPT/anatomy bundle to VVAULT-owned file/body storage:

- `identity/prompt.json`
- projected identity files such as `conditioning.txt`, `definition.txt`, and `voice.json`
- `config/metadata.json`
- a seeded Chatty transcript row

Capsules remain a separate feature surface. The canonical create route does not scaffold an empty capsule by default.

## Relationship To Other Systems

### Preview

The web server preview layer treats `.capsule` rows as structured previewable content when possible. Previewing a capsule does not make that capsule canonical.

### VXRunner

`vvault/server/vxrunner_baseline.py` can transform capsule-like data into forensic baseline JSON. This is a downstream use of capsule data, not the capsule definition itself.

### User Capsules

`vvault/memory/user_capsule_forge.py` describes user-oriented capsule concepts. Treat it as a separate surface from construct memup capsules unless current runtime code proves they share a path.

## Current Guidance

When adding or auditing capsule behavior:

1. Name which surface is being handled: CapsuleForge v1, memup_sync v2, CapsuleForge v3, local VVAULTCore, preview, or VXRunner.
2. Do not treat every `.capsule` as the same artifact type.
3. Do not overwrite original capsule rows as part of preview or repair.
4. Prefer derived/materialized siblings for generated readable artifacts.
5. Keep source manifests separate from storage paths.
6. Keep raw transcripts in transcript rows, not capsule bodies.
7. Mark legacy docs/tests as legacy unless current code proves they are live.
