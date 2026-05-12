"""
Pocketverse runtime authority guard.

Enforces that destructive and identity-changing actions on Pocketverse-anchored
constructs are only performed by callers allowed by the Higher Plane manifest
(custodian). Uses existing identity/session plumbing; no new auth frameworks.
"""

import os
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class PocketverseAuthorityError(Exception):
    """Raised when Pocketverse authority checks fail."""
    pass


def _get_metadata_from_registry(construct_id: str) -> Optional[Dict[str, Any]]:
    """Load construct metadata from engine ConstructRegistry (filesystem)."""
    try:
        from vvault.engine.orchestration.construct_registry import get_registry
        registry = get_registry()
        return registry.to_dict(construct_id)
    except Exception as e:
        logger.debug("Registry metadata lookup failed for %s: %s", construct_id, e)
        return None


def _get_metadata_from_body(construct_id: str, request_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Load construct metadata from a VVAULT-native metadata loader."""
    metadata_loader = request_context.get("metadata_loader")
    if not callable(metadata_loader):
        return None
    try:
        metadata = metadata_loader(construct_id)
        return metadata if isinstance(metadata, dict) else None
    except Exception as e:
        logger.debug("VVAULT body metadata lookup failed for %s: %s", construct_id, e)
        return None


def _is_anchored(meta: Optional[Dict[str, Any]]) -> bool:
    return bool(meta and meta.get("pocketverse_anchored") is True)


def _get_allowed_emails() -> set:
    """Custodian authority = admin emails (reuse existing plumbing)."""
    raw = os.environ.get("VVAULT_ADMIN_EMAILS", "admin@vvault.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def enforce_pocketverse_authority(construct_id: str, request_context: Dict[str, Any]) -> None:
    """
    Enforce Pocketverse authority for anchored constructs.
    If the construct is not pocketverse_anchored, returns without doing anything.
    If anchored, verifies the caller is allowed by the Higher Plane manifest (custodian).
    Raises PocketverseAuthorityError("POCKETVERSE_AUTHORITY_DENIED") on mismatch or missing identity.
    """
    if not construct_id or not isinstance(construct_id, str):
        return
    construct_id = construct_id.strip().lower()

    # Load metadata: registry first, then VVAULT body metadata.
    meta = _get_metadata_from_registry(construct_id)
    if not meta or not _is_anchored(meta):
        meta = _get_metadata_from_body(construct_id, request_context)
    if not _is_anchored(meta):
        return

    # Derive caller from request_context (reuse server identity)
    caller = (
        request_context.get("email")
        or request_context.get("user_id")
        or request_context.get("session_user")
    )
    if isinstance(caller, dict):
        caller = caller.get("email") or caller.get("user_id")
    if not caller:
        logger.warning("Pocketverse guard: anchored construct %s but no caller identity", construct_id)
        raise PocketverseAuthorityError("POCKETVERSE_AUTHORITY_DENIED")

    caller = str(caller).strip().lower()

    # Verify against custodian: use Higher Plane manifest if available
    try:
        from vvault.layers.layer1_higher_plane import load_layer1_manifest
        manifest = load_layer1_manifest(construct_id)
        if manifest and manifest.get("custodian"):
            # Custodian authority = admin list (same as sovereign)
            allowed = caller in _get_allowed_emails()
            if allowed:
                return
            logger.warning(
                "Pocketverse guard: caller %s not allowed for anchored construct %s (custodian: %s)",
                caller, construct_id, manifest.get("custodian"),
            )
            raise PocketverseAuthorityError("POCKETVERSE_AUTHORITY_DENIED")
    except PocketverseAuthorityError:
        raise
    except Exception as e:
        logger.warning("Pocketverse guard: could not load Layer 1 manifest for %s: %s", construct_id, e)

    # Allow only custodian (admin list)
    if caller in _get_allowed_emails():
        return
    raise PocketverseAuthorityError("POCKETVERSE_AUTHORITY_DENIED")
