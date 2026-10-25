"""Canonical Chatty construct classification contract.

OVVAULTS owns construct category metadata.  This module defines the protected
membership contract used to validate that metadata; sharing an inference route
does not grant system status.  Unknown/user-created constructs are always User
Constructs until an explicit contract revision names them otherwise.
"""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

TAXONOMY_VERSION = 9

_MEMBERSHIP = {
    "system": (
        "continuitygpt-001",
        "lin-001",
        "linda-001",
        "tom-001",
        "valorie-001",
        "zen-001",
    ),
    "hydro": (
        "arbiter-001",
        "cleangpt-001",
        "click-001",
        "codegpt-001",
        "day-day-001",
        "engineergpt-001",
        "hypatia-001",
        "insight-001",
        "luna-001",
        "quality-control-001",
        "scout-001",
    ),
    "user": (
        "katana-001",
        "katana-002",
        "nova-001",
        "sera-001",
    ),
}

WITHHELD_CONSTRUCTS = frozenset()
# K.A.T.A.N.A. is a callable Hydro composition, not an instance directory.
# Its two user-owned component AIs remain canonical records and retain their
# own identity, capsule, and transcript authority.
_COMPOSITIONS = {
    "katana-team": {
        "display_name": "K.A.T.A.N.A.",
        "category": "hydro",
        "owner_scope": "account",
        "runtime": "synthesized_orchestration",
        "components": ("katana-001", "katana-002"),
    },
}

COMPOSITIONS = MappingProxyType({
    key: MappingProxyType(dict(value)) for key, value in _COMPOSITIONS.items()
})
# Owner constructs are never hidden by a globally catalogued basename. UI
# compositions may coexist with their independently authored owner instances.
HIDDEN_SELECTOR_CONSTRUCTS = frozenset({"katana-001", "katana-002"})

MEMBERSHIP = MappingProxyType({key: tuple(value) for key, value in _MEMBERSHIP.items()})
CATEGORY_BY_CONSTRUCT = MappingProxyType(
    {
        construct_id: category
        for category, construct_ids in MEMBERSHIP.items()
        for construct_id in construct_ids
    }
)
PROTECTED_DELETION_IDS = frozenset(CATEGORY_BY_CONSTRUCT)
PROTECTED_DELETION_BASES = frozenset(
    construct_id.rsplit("-", 1)[0]
    for construct_id in PROTECTED_DELETION_IDS
)

if len(CATEGORY_BY_CONSTRUCT) != sum(len(values) for values in MEMBERSHIP.values()):
    raise RuntimeError("Canonical construct taxonomy contains duplicate membership")

_HASH_INPUT = json.dumps(
    {
        "version": TAXONOMY_VERSION,
        "membership": {category: list(ids) for category, ids in MEMBERSHIP.items()},
        "compositions": {
            construct_id: {
                **dict(composition),
                "components": list(composition["components"]),
            }
            for construct_id, composition in COMPOSITIONS.items()
        },
        "hidden_selector_constructs": sorted(HIDDEN_SELECTOR_CONSTRUCTS),
        "withheld_constructs": sorted(WITHHELD_CONSTRUCTS),
    },
    sort_keys=True,
    separators=(",", ":"),
)
TAXONOMY_SHA256 = hashlib.sha256(_HASH_INPUT.encode("utf-8")).hexdigest()


def normalize_construct_id(value: object) -> str:
    return str(value or "").strip().lower()


def canonical_category(construct_id: object) -> str:
    """Return catalog membership, defaulting unregistered constructs to user.

    Catalog membership is not ownership of an account-scoped instance path.
    Use ``canonical_category_for_scope`` when validating persisted rows.
    """

    return CATEGORY_BY_CONSTRUCT.get(normalize_construct_id(construct_id), "user")


def canonical_category_for_scope(
    construct_id: object,
    *,
    system_scope: bool,
) -> str:
    """Classify a construct inside its owner scope.

    The protected catalog applies only to system-owned rows. An ordinary
    owner's instance may use the same callsign without becoming the catalog
    archetype or inheriting its lifecycle/deletion authority.
    """

    return canonical_category(construct_id) if system_scope else "user"


def canonical_category_for_record(
    construct_id: object,
    *,
    system_scope: bool,
    stored_category: object,
) -> str:
    """Classify a persisted record without turning Hydro into System.

    System identity is proven by the protected ``is_system`` scope. Hydro AIs
    remain account-owned records, but an explicitly stored Hydro category is
    valid only when the canonical Hydro roster contains that exact callsign.
    Ordinary owner records—including callsign collisions—remain User AIs.
    """

    normalized_category = str(stored_category or "").strip().lower()
    catalog_category = canonical_category(construct_id)
    if system_scope:
        return catalog_category
    if normalized_category == "system" and catalog_category == "system":
        return "system"
    if normalized_category == "hydro" and catalog_category == "hydro":
        return "hydro"
    return "user"


def is_protected_from_deletion(
    construct_id: object,
    *,
    system_scope: bool = True,
) -> bool:
    """Protect catalog IDs only when the target rows are system-scoped."""

    normalized = normalize_construct_id(construct_id)
    return system_scope and (
        normalized in PROTECTED_DELETION_IDS
        or normalized in PROTECTED_DELETION_BASES
    )


def category_is_canonical(construct_id: object, category: object) -> bool:
    return str(category or "").strip().lower() == canonical_category(construct_id)


def category_is_canonical_for_scope(
    construct_id: object,
    category: object,
    *,
    system_scope: bool,
) -> bool:
    return (
        str(category or "").strip().lower()
        == canonical_category_for_scope(construct_id, system_scope=system_scope)
    )


def taxonomy_payload() -> dict[str, object]:
    return {
        "success": True,
        "status": "body_native",
        "canonical": True,
        "authority": "vvault_body",
        "persistence_owner": "ovvaults.vault_files",
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_sha256": TAXONOMY_SHA256,
        "membership": {key: list(value) for key, value in MEMBERSHIP.items()},
        "compositions": {
            construct_id: {
                **dict(composition),
                "components": list(composition["components"]),
            }
            for construct_id, composition in COMPOSITIONS.items()
        },
        "hidden_selector_constructs": sorted(HIDDEN_SELECTOR_CONSTRUCTS),
        "withheld_constructs": sorted(WITHHELD_CONSTRUCTS),
        "unknown_construct_default": "user",
        "catalog_membership_scope": "system_owner_only",
        "owner_instance_default": "user",
    }
