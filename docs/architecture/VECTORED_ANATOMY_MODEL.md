# VVAULT Vectored Anatomy Model

## Status

Canonical product-domain model. Devon's definition is product canon and overrides narrower construct-only or memory-only descriptions.

## Canonical Definition

VVAULT keeps the double acronym:

- **Voice & Vaulted Autonomy for Unfragmented Long-Term Tethering**: the continuity, voice, autonomy, and tethering layer.
- **Verified Vectored Anatomy Unconsciously Lingering Together**: the product-domain model for protected anatomy bodies.

A **Vectored Anatomy** is a protected, identity-bearing directory body. It can represent a human, AI, product, project, repository, place, object, organization, service, or system.

VVAULT is an advanced vault/drive for these anatomies, combining directory storage, capsule/glyph identity, security history, witness history, permissions, integrations, and recovery/rematerialization support.

## Model Terms

| Term | Meaning |
|------|---------|
| Vectored Anatomy | A protected, identity-bearing directory body. |
| Anatomy type | The kind of body: human, AI, product, project, repository, place, object, organization, service, or system. |
| Directory body | The file tree, records, metadata, permissions, and paths that make the anatomy addressable. |
| Identity vector | A persisted identity-bearing signal, such as name, callsign, prompt, definition, voice, avatar, glyph, or provider identity. |
| Capsule snapshot | A portable or versioned snapshot of memory, identity, state, or recovery material inside an anatomy. |
| Glyph mark | A visual identity/authenticity marker attached to an anatomy. |
| Witness history | Audit, authority, continuity, security, and chain-of-custody records for an anatomy. |
| Integration surface | A provider or service that contributes artifacts or state into an anatomy. |
| Rematerialization support | Recovery material, witness records, and continuity metadata used to restore or reconstitute an anatomy. |

## Current Implementation Mapping

| Current feature | Vectored Anatomy interpretation |
|-----------------|---------------------------------|
| `vault_files` | Physical persistence for anatomy artifacts. |
| `storage_path` | Directory-body path within an anatomy. |
| `construct_id` | Compatibility subject key for current AI/VSI anatomies. |
| `instances/{callsign}/` | Current AI/VSI anatomy directory body. |
| `identity/` files | Identity vectors. |
| `{callsign}_glyph.png` and auth glyphs | Glyph marks. |
| `memup/*.capsule` and capsule routes | Capsule snapshots. |
| Chatty transcript routes | Integration/provider ingestion. |
| Identity projection routes | Explicit identity vector projection. |
| Audit DB/logs and privileged event records | Security and witness history. |
| Pocketverse guard and layer manifests | Authority and continuity witness controls. |
| Continuity ledger and rematerialization seed | Recovery/rematerialization support. |
| `frame/` | Body, neural, and memory anatomy components. |
| Service configs and credentials | Service/system anatomy support. |

## Compatibility Contract

Do not rename current routes, folders, Supabase columns, `construct_id`, `instances`, `capsules`, or `vault_files` for language alignment alone.

Current construct memory features remain valid as an AI/VSI anatomy subtype. They are not the full product definition.

Future neutral anatomy metadata should be layered in non-breakingly, for example `anatomy_type` and `anatomy_id` metadata alongside existing compatibility keys.

## Documentation Guidance

Use VVAULT's broad product definition when describing the whole system: an advanced vault/drive for protected, identity-bearing directory bodies.

Use construct, capsule, memory, and legacy vessel language only when referring to the current AI/VSI feature subtype, historical notes, or specific capsule mechanics.

Legal, privacy, and operations docs may describe storage, archive, privacy, and compliance behavior, but should not narrow VVAULT to AI-only memory management.
