"""Pure canonical artifact identifiers and paths shared by VVAULT consumers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CONTRACT_ID = "life.vvault.data-layout"
CONTRACT_VERSION = "1.0.0"
PROMPT_ARTIFACT_ID = "life.vvault.identity.prompt"
CAPSULE_ARTIFACT_ID = "life.vvault.memup.capsule"
REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "v1"
    / "artifact-registry.json"
)


def registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def normalize_instance_id(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def artifact_spec(artifact_id: str) -> dict[str, Any]:
    spec = next(
        (
            item
            for item in registry()["artifacts"]
            if item["artifact_id"] == artifact_id
        ),
        None,
    )
    if not spec:
        raise KeyError(artifact_id)
    return spec


def relative_path(artifact_id: str, instance_id: str) -> str:
    callsign = normalize_instance_id(instance_id)
    template = artifact_spec(artifact_id)["canonical_path"]
    return (
        template.replace("{canonical-instance-id}", callsign)
        .replace("{bare-name}", re.sub(r"-[0-9]{3}$", "", callsign))
    )


def storage_path(artifact_id: str, instance_id: str) -> str:
    callsign = normalize_instance_id(instance_id)
    return f"instances/{callsign}/{relative_path(artifact_id, callsign)}"
