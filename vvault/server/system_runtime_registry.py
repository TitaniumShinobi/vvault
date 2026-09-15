"""Protected non-intrinsic system-runtime registry and bundled profile loader.

System runtimes are VVAULT principals, but they are not LIFE constructs.  This
module deliberately lives outside ``construct_taxonomy`` so a protected
runtime callsign cannot acquire construct membership, identity hydration, or a
provider/model configuration by implication.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any

SYSTEM_RUNTIME_REGISTRY_VERSION = 1
AUTO_RUNTIME_PRINCIPAL_ID = "auto-001"
AUTO_PROFILE_CONTRACT = "chatty-auto-system-runtime-profile/v1"
AUTO_PROFILE_REVISION = "1.0.0"
AUTO_PROFILE_COMBINED_SHA256 = (
    "c046cd0ea1c5215b810babf50b682a576e6fd2deb2fc7d63190a4dfb575791c5"
)

_CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts" / "v1"
AUTO_PROFILE_TEMPLATE_ROOT = (
    _CONTRACT_ROOT / "templates" / "system-runtimes" / AUTO_RUNTIME_PRINCIPAL_ID
)

_AUTO_DESCRIPTOR = {
    "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
    "name": "AUTO",
    "expansion": "Accurate Unbiased Transforming Orchestration",
    "principalType": "system_runtime",
    "profileType": "non_intrinsic_system_runtime",
    "intrinsicIdentity": False,
    "behaviorAuthority": "chatty-core",
    "canonicalAuthority": "vvault/ovvaults",
    "provider": None,
    "model": None,
    "profileContract": AUTO_PROFILE_CONTRACT,
    "profileRevision": AUTO_PROFILE_REVISION,
    "combinedSha256": AUTO_PROFILE_COMBINED_SHA256,
    "requiredArtifactIds": (
        "life.vvault.system-runtime.prompt",
        "life.vvault.system-runtime.definition",
        "life.vvault.system-runtime.conditioning",
    ),
}

SYSTEM_RUNTIME_REGISTRY = MappingProxyType(
    {AUTO_RUNTIME_PRINCIPAL_ID: MappingProxyType(dict(_AUTO_DESCRIPTOR))}
)


class SystemRuntimeProfileError(ValueError):
    """The reviewed bundled system-runtime profile failed closed validation."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(name: str) -> dict[str, Any]:
    path = AUTO_PROFILE_TEMPLATE_ROOT / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemRuntimeProfileError(f"AUTO profile artifact is unreadable: {name}") from exc
    if not isinstance(value, dict):
        raise SystemRuntimeProfileError(f"AUTO profile artifact must be an object: {name}")
    return value


def _read_conditioning() -> str:
    path = AUTO_PROFILE_TEMPLATE_ROOT / "conditioning.txt"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SystemRuntimeProfileError("AUTO conditioning artifact is unreadable") from exc
    canonical = raw.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
    if raw != canonical:
        raise SystemRuntimeProfileError("AUTO conditioning bytes are not canonical LF text")
    return canonical


def system_runtime_descriptor(runtime_principal_id: object) -> dict[str, Any] | None:
    descriptor = SYSTEM_RUNTIME_REGISTRY.get(str(runtime_principal_id or "").strip().lower())
    return deepcopy(dict(descriptor)) if descriptor else None


def is_declared_system_runtime(
    runtime_principal_id: object,
    *,
    system_scope: bool,
) -> bool:
    """Return true only for a protected global system-runtime record.

    An owner-created construct may use the same callsign without inheriting
    this classification, protection, profile, or authority.
    """

    return bool(system_scope) and system_runtime_descriptor(runtime_principal_id) is not None


def principal_type_for_record(
    principal_id: object,
    *,
    system_scope: bool,
) -> str:
    return (
        "system_runtime"
        if is_declared_system_runtime(principal_id, system_scope=system_scope)
        else "construct"
    )


def is_protected_system_runtime(
    runtime_principal_id: object,
    *,
    system_scope: bool,
) -> bool:
    return is_declared_system_runtime(runtime_principal_id, system_scope=system_scope)


def load_bundled_auto_profile() -> dict[str, Any]:
    """Load and verify the immutable Plan 1.5 creation template.

    Returned documents are safe copies.  The template remains
    ``template_verified``; callers may claim ``canonical_verified`` only after
    an atomic canonical write and exact readback.
    """

    prompt = _read_json("prompt.json")
    definition = _read_json("definition.json")
    conditioning = _read_conditioning()
    manifest = _read_json("profile.json")

    expected_manifest = {
        "contract": AUTO_PROFILE_CONTRACT,
        "revision": AUTO_PROFILE_REVISION,
        "runtimePrincipalId": AUTO_RUNTIME_PRINCIPAL_ID,
        "profileType": "non_intrinsic_system_runtime",
        "intrinsicIdentity": False,
        "behaviorAuthority": "chatty-core",
        "canonicalAuthority": "vvault/ovvaults",
        "provider": None,
        "model": None,
        "verificationState": "template_verified",
        "canonicalPersistence": False,
    }
    for key, expected in expected_manifest.items():
        if manifest.get(key) != expected:
            raise SystemRuntimeProfileError(f"AUTO profile manifest mismatch: {key}")
    if prompt.get("runtime_principal_id") != AUTO_RUNTIME_PRINCIPAL_ID:
        raise SystemRuntimeProfileError("AUTO prompt principal mismatch")
    if prompt.get("intrinsicIdentity") is not False or prompt.get("provider") is not None or prompt.get("model") is not None:
        raise SystemRuntimeProfileError("AUTO prompt violates the non-intrinsic provider-free boundary")
    if definition.get("instance_id") != AUTO_RUNTIME_PRINCIPAL_ID:
        raise SystemRuntimeProfileError("AUTO definition principal mismatch")

    hashes = {
        "promptSha256": _sha256_text(_canonical_json(prompt)),
        "definitionSha256": _sha256_text(_canonical_json(definition)),
        "conditioningSha256": _sha256_text(conditioning),
        "combinedSha256": _sha256_text(
            _canonical_json(
                {
                    "conditioning": conditioning,
                    "definition": definition,
                    "prompt": prompt,
                }
            )
        ),
    }
    if manifest.get("hashes") != hashes:
        raise SystemRuntimeProfileError("AUTO profile artifact hashes do not match the reviewed manifest")
    if hashes["combinedSha256"] != AUTO_PROFILE_COMBINED_SHA256:
        raise SystemRuntimeProfileError("AUTO profile combined hash is unsupported")

    provenance = manifest.get("provenanceSources")
    if not isinstance(provenance, list) or not provenance:
        raise SystemRuntimeProfileError("AUTO profile provenance is absent")
    return {
        "descriptor": system_runtime_descriptor(AUTO_RUNTIME_PRINCIPAL_ID),
        "manifest": deepcopy(manifest),
        "prompt": deepcopy(prompt),
        "definition": deepcopy(definition),
        "conditioning": conditioning,
        "canonicalBytes": {
            "prompt": _canonical_json(prompt).encode("utf-8"),
            "definition": _canonical_json(definition).encode("utf-8"),
            "conditioning": conditioning.encode("utf-8"),
        },
        "hashes": hashes,
        "provenanceSources": deepcopy(provenance),
    }


def validate_bundled_auto_profile() -> bool:
    load_bundled_auto_profile()
    return True
