"""Versioned canonical VVAULT artifact validation and migration planning.

Discovery is read-only.  The planner never mutates OVVAULTS and never chooses a
winner when multiple source records can represent the same semantic artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from vvault.server import chatty_body_service
from vvault.server.canonical_transform import CANONICAL_BUCKET, transform_content
from vvault.server.system_runtime_registry import is_declared_system_runtime

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts" / "v1"
REGISTRY_PATH = CONTRACT_ROOT / "artifact-registry.json"
INSTANCE_PATTERN = re.compile(r"(?:^|/)instances/([^/]+)/(.+)$")
USER_WORKSPACE_PATTERN = re.compile(
    r"(?:^|/)(account|library|cleanhouse|system)(?:/|$)"
)


def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def load_schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted((CONTRACT_ROOT / "schemas").glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema_id = str(schema.get("$id") or "").split("/", 1)[0]
        if schema_id:
            schemas[schema_id] = schema
    return schemas


def canonical_instance_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def relative_instance_path(row: dict[str, Any]) -> tuple[str | None, str | None]:
    # filename is the authored logical identity.  storage_path/object_key may
    # contain compatibility routing from an old import and must not collapse
    # two separately named transcript threads into one artifact.
    for candidate in (
        row.get("filename"),
        row.get("storage_path"),
        row.get("object_key"),
    ):
        match = INSTANCE_PATTERN.search(str(candidate or "").strip())
        if match:
            return canonical_instance_id(match.group(1)), match.group(2)
    return None, None


def render_path(template: str, instance_id: str) -> str:
    return (
        template.replace("{canonical-instance-id}", instance_id)
        .replace("{bare-name}", re.sub(r"-[0-9]{3}$", "", instance_id))
    )


def path_matches_template(path: str, template: str) -> bool:
    pattern = re.escape(template)
    pattern = pattern.replace(re.escape("{canonical-instance-id}"), r"[^/]+")
    pattern = pattern.replace(re.escape("{bare-name}"), r"[^/]+")
    return re.fullmatch(pattern, path) is not None


def _record_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "user_id": str(row.get("user_id") or ""),
        "construct_id": str(row.get("construct_id") or ""),
        "filename": str(row.get("filename") or ""),
        "object_key": str(row.get("object_key") or ""),
        "storage_path": str(row.get("storage_path") or ""),
        "sha256": str(row.get("sha256") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def _json_object(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _has_material_content(row: dict[str, Any]) -> bool:
    if "content" not in row:
        return True
    content = row.get("content")
    return content is not None and (not isinstance(content, str) or bool(content.strip()))


def _prompt_core(row: dict[str, Any]) -> tuple[str, str, str] | None:
    value = _json_object(row.get("content"))
    if not value:
        return None
    name = str(
        value.get("name")
        or value.get("displayName")
        or value.get("display_name")
        or ""
    ).strip()
    description = str(value.get("description") or "").strip()
    instructions = str(value.get("instructions") or "").strip()
    return (name, description, instructions) if instructions else None


def _projection_rank(row: dict[str, Any], *, instance_id: str, relative_path: str) -> int:
    object_key = str(row.get("object_key") or "").strip("/")
    bucket = str(row.get("bucket") or "")
    logical_path = f"instances/{instance_id}/{relative_path}"
    user_id = str(row.get("user_id") or "").strip()
    if (
        bucket == CANONICAL_BUCKET
        and user_id
        and object_key == f"users/{user_id}/{logical_path}"
    ):
        return 400
    if user_id and object_key == f"users/{user_id}/{logical_path}":
        return 300
    if object_key == logical_path:
        return 200
    if "#source:" in object_key:
        return 100
    return 0


def _issue(
    code: str,
    instance_id: str,
    artifact_id: str | None,
    severity: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "instance_id": instance_id,
        "artifact_id": artifact_id,
        **details,
    }


def _json_type_matches(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, True)


def validate_json_document(
    value: Any,
    schema: dict[str, Any],
    *,
    location: str = "$",
) -> list[str]:
    """Validate the contract's deliberately bounded JSON Schema vocabulary."""
    reference = schema.get("$ref")
    if isinstance(reference, str):
        # Context manifests use same-directory schema composition. Resolve only
        # a plain local filename; remote, fragment, and traversal references are
        # deliberately outside this bounded validator's authority.
        if (
            not reference.endswith(".schema.json")
            or Path(reference).name != reference
            or "/" in reference
            or "\\" in reference
        ):
            return [f"{location}: unsupported schema reference"]
        path = CONTRACT_ROOT / "schemas" / reference
        try:
            resolved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [f"{location}: schema reference is unavailable"]
        return validate_json_document(value, resolved, location=location)
    errors: list[str] = []
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected] if expected else []
    if expected_types and not any(_json_type_matches(value, item) for item in expected_types):
        return [f"{location}: expected {' or '.join(expected_types)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: must be one of {schema['enum']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{location}: shorter than minLength")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{location}: does not match {schema['pattern']}")
        if schema.get("format") == "uuid":
            try:
                uuid.UUID(value)
            except (ValueError, AttributeError):
                errors.append(f"{location}: invalid UUID")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{location}: invalid RFC 3339 date-time")
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        for name in sorted(required - set(value)):
            errors.append(f"{location}.{name}: required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for name in sorted(set(value) - set(properties)):
                errors.append(f"{location}.{name}: unknown field")
        for name, child in value.items():
            if name in properties:
                errors.extend(
                    validate_json_document(child, properties[name], location=f"{location}.{name}")
                )
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            errors.extend(
                validate_json_document(child, schema["items"], location=f"{location}[{index}]")
            )
    return errors


def audit_rows(rows: Iterable[dict[str, Any]], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = list(rows)
    contract = registry or load_registry()
    schemas = load_schemas()
    artifacts = contract["artifacts"]
    deprecated_paths = {
        render_path(path, "")
        for item in contract.get("deprecated_duplicates", [])
        for path in item.get("paths", [])
    }
    # Scope is part of canonical identity.  A protected global runtime and
    # owner-created callsign collisions must never share candidates, satisfy
    # one another's requirements, or appear as duplicate artifacts.
    by_principal: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    issues: list[dict[str, Any]] = []
    workspace_record_count = 0

    for row in rows:
        path_instance, relative = relative_instance_path(row)
        column_instance = canonical_instance_id(row.get("construct_id"))
        instance_id = path_instance or column_instance
        if not instance_id or not relative:
            raw_path = str(row.get("storage_path") or row.get("object_key") or row.get("filename") or "")
            if USER_WORKSPACE_PATTERN.search(raw_path):
                workspace_record_count += 1
            else:
                issues.append(
                    _issue(
                        "unclassified_record",
                        instance_id,
                        None,
                        "warning",
                        record=_record_ref(row),
                    )
                )
            continue
        principal_scope = (
            "system"
            if bool(row.get("is_system"))
            else f"owner:{str(row.get('user_id') or '').strip()}"
        )
        by_principal[(instance_id, principal_scope)].append((relative, row))
        if relative in deprecated_paths:
            issues.append(
                _issue(
                    "deprecated_duplicate_projection",
                    instance_id,
                    None,
                    "warning",
                    actual_path=relative,
                    disposition="preserve-history-no-read-write-authority",
                    record=_record_ref(row),
                )
            )
        # System-owned product projections may intentionally encode the
        # instance only in their path (for example Codex's latest-thread
        # pointer). They are not identity artifacts and must not be rewritten
        # as construct-owned records. All other path/column disagreements are
        # ownership errors.
        system_projection = bool(row.get("is_system")) and not column_instance
        if path_instance != column_instance and not system_projection:
            issues.append(
                _issue(
                    "ownership_id_mismatch",
                    instance_id,
                    None,
                    "error",
                    path_instance_id=path_instance,
                    construct_id=column_instance,
                    record=_record_ref(row),
                )
            )
        if not re.fullmatch(contract["identifier_patterns"]["canonical-instance-id"], instance_id):
            issues.append(_issue("invalid_instance_id", instance_id, None, "error", record=_record_ref(row)))

    for (instance_id, principal_scope), instance_rows in sorted(by_principal.items()):
        declared_runtime = is_declared_system_runtime(
            instance_id,
            system_scope=principal_scope == "system",
        )
        for artifact in artifacts:
            artifact_id = artifact["artifact_id"]
            runtime_artifact = (
                artifact.get("requirement") == "required-for-declared-system-runtime"
            )
            # A protected system runtime is not a LIFE construct.  Its global
            # system rows are audited only against the system-runtime profile
            # family; an ordinary owner-created callsign collision is audited
            # independently as a construct and receives no runtime authority.
            if declared_runtime:
                if runtime_artifact:
                    applicable_rows = instance_rows
                else:
                    continue
            else:
                if runtime_artifact:
                    continue
                applicable_rows = instance_rows
            canonical = render_path(artifact["canonical_path"], instance_id)
            aliases = {render_path(alias, instance_id) for alias in artifact["legacy_aliases"]}
            all_exact = [(path, row) for path, row in applicable_rows if path == canonical]
            # A null inline `content` value does not prove the artifact is
            # empty: many OVVAULTS rows are object-storage-backed. Presence,
            # identity, and ranking come from the canonical record columns;
            # schema validation runs only when inline JSON is materialized.
            exact = list(all_exact)
            casefold = [(path, row) for path, row in applicable_rows if path.casefold() == canonical.casefold()]
            legacy = [
                (path, row)
                for path, row in applicable_rows
                if path in aliases
            ]

            required_for_principal = artifact["requirement"] == "required" or (
                runtime_artifact and declared_runtime
            )
            if required_for_principal and not exact:
                issues.append(
                    _issue(
                        "missing_required_artifact",
                        instance_id,
                        artifact_id,
                        "error",
                        canonical_path=canonical,
                    )
                )
            if len(exact) > 1:
                hashes = sorted({str(row.get("sha256") or "") for _, row in exact})
                ranked = sorted(
                    [
                        (
                        _projection_rank(
                            row,
                            instance_id=instance_id,
                            relative_path=canonical,
                        ),
                        row,
                        )
                        for _, row in exact
                    ],
                    key=lambda item: item[0],
                )
                authoritative = (
                    ranked[-1][1]
                    if len(ranked) > 1 and ranked[-1][0] > ranked[-2][0]
                    else None
                )
                prompt_cores = (
                    {_prompt_core(row) for _, row in exact}
                    if artifact_id == "life.vvault.identity.prompt"
                    else set()
                )
                equivalent_prompt_generations = (
                    artifact_id == "life.vvault.identity.prompt"
                    and None not in prompt_cores
                    and len(prompt_cores) == 1
                )
                issues.append(
                    _issue(
                        (
                            "historical_projection_rows"
                            if authoritative
                            else "equivalent_prompt_generations"
                            if equivalent_prompt_generations
                            else "duplicate_canonical_artifact"
                        ),
                        instance_id,
                        artifact_id,
                        (
                            "warning"
                            if authoritative or equivalent_prompt_generations or len(hashes) == 1
                            else "collision"
                        ),
                        canonical_path=canonical,
                        distinct_hashes=hashes,
                        authoritative_record_id=(
                            str(authoritative.get("id") or "")
                            if authoritative else None
                        ),
                        records=[_record_ref(row) for _, row in exact],
                    )
                )
                if authoritative:
                    exact = [
                        (path, row)
                        for path, row in exact
                        if str(row.get("id") or "") == str(authoritative.get("id") or "")
                    ]
            for path, row in casefold:
                if path != canonical:
                    issues.append(
                        _issue(
                            "incorrect_casing",
                            instance_id,
                            artifact_id,
                            "error",
                            actual_path=path,
                            canonical_path=canonical,
                            canonical_present=bool(exact),
                            record=_record_ref(row),
                        )
                    )
            for path, row in legacy:
                issues.append(
                    _issue(
                        "legacy_alias",
                        instance_id,
                        artifact_id,
                        "warning",
                        actual_path=path,
                        canonical_path=canonical,
                        canonical_present=bool(exact),
                        record=_record_ref(row),
                    )
                )
            # A legacy source beside a canonical artifact is history, not a
            # second canonical value.  It is migratable only when canonical is
            # absent, so prompt.txt can never become a second instructions
            # field inside prompt.json.
            schema_id = artifact.get("schema_id")
            schema = schemas.get(schema_id) if schema_id else None
            for path, row in exact:
                if not schema:
                    continue
                raw_content = row.get("content")
                if raw_content is None:
                    continue
                try:
                    document = (
                        raw_content
                        if isinstance(raw_content, dict)
                        else json.loads(str(raw_content or ""))
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    issues.append(
                        _issue(
                            "invalid_json",
                            instance_id,
                            artifact_id,
                            "error",
                            canonical_path=path,
                            record=_record_ref(row),
                        )
                    )
                    continue
                schema_errors = validate_json_document(document, schema)
                if schema_errors:
                    issues.append(
                        _issue(
                            "schema_violation",
                            instance_id,
                            artifact_id,
                            "error",
                            canonical_path=path,
                            errors=schema_errors,
                            record=_record_ref(row),
                        )
                    )

        for dynamic in contract.get("dynamic_artifacts", []):
            if dynamic["artifact_id"] != "life.vvault.config.action":
                continue
            action_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for relative, row in instance_rows:
                if re.fullmatch(r"config/actions/[a-z0-9][a-z0-9_-]*\.json", relative):
                    action_rows[relative].append(row)
            schema = schemas.get(dynamic.get("schema_id"))
            for relative, candidates in sorted(action_rows.items()):
                if len(candidates) > 1:
                    hashes = sorted({str(row.get("sha256") or "") for row in candidates})
                    issues.append(
                        _issue(
                            "duplicate_dynamic_artifact",
                            instance_id,
                            dynamic["artifact_id"],
                            "warning" if len(hashes) == 1 else "collision",
                            canonical_path=relative,
                            distinct_hashes=hashes,
                            records=[_record_ref(row) for row in candidates],
                        )
                    )
                    if len(hashes) != 1:
                        continue
                row = sorted(
                    candidates,
                    key=lambda candidate: _projection_rank(
                        candidate,
                        instance_id=instance_id,
                        relative_path=relative,
                    ),
                    reverse=True,
                )[0]
                raw_content = row.get("content")
                if raw_content is None or not schema:
                    continue
                try:
                    document = (
                        raw_content
                        if isinstance(raw_content, dict)
                        else json.loads(str(raw_content))
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    issues.append(
                        _issue(
                            "invalid_json",
                            instance_id,
                            dynamic["artifact_id"],
                            "error",
                            canonical_path=relative,
                            record=_record_ref(row),
                        )
                    )
                    continue
                expected_action_name = relative.rsplit("/", 1)[-1][:-5]
                schema_errors = validate_json_document(document, schema)
                if document.get("actionName") != expected_action_name:
                    schema_errors.append(
                        f"$.actionName: must equal filename stem {expected_action_name!r}"
                    )
                if schema_errors:
                    issues.append(
                        _issue(
                            "schema_violation",
                            instance_id,
                            dynamic["artifact_id"],
                            "error",
                            canonical_path=relative,
                            errors=schema_errors,
                            record=_record_ref(row),
                        )
                    )

    counts = defaultdict(int)
    severity_counts = defaultdict(int)
    for issue in issues:
        counts[issue["code"]] += 1
        severity_counts[issue["severity"]] += 1
    blocking_issue_count = sum(
        1 for issue in issues if issue["severity"] in {"error", "collision"}
    )
    return {
        "report_id": "life.vvault.canonical-discrepancy",
        "report_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authority": contract["canonical_authority"],
        "mode": "read-only",
        "instance_count": len({instance_id for instance_id, _ in by_principal}),
        "audited_principal_count": len(by_principal),
        "audited_record_count": len(rows),
        "record_count": sum(len(value) for value in by_principal.values()),
        "workspace_record_count": workspace_record_count,
        "issue_count": len(issues),
        "blocking_issue_count": blocking_issue_count,
        "conforms": blocking_issue_count == 0,
        "counts_by_code": dict(sorted(counts.items())),
        "counts_by_severity": dict(sorted(severity_counts.items())),
        "issues": issues,
    }


def _candidate_rows_for_target(
    rows: list[dict[str, Any]],
    *,
    instance_id: str,
    artifact: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    canonical = render_path(artifact["canonical_path"], instance_id)
    aliases = {render_path(alias, instance_id) for alias in artifact["legacy_aliases"]}
    candidates = []
    for row in rows:
        row_instance, relative = relative_instance_path(row)
        if row_instance == instance_id and relative in {canonical, *aliases}:
            candidates.append((relative, row))
    return candidates


def _related_source_rows(
    rows: list[dict[str, Any]],
    *,
    instance_id: str,
    artifact_id: str,
) -> list[dict[str, Any]]:
    preferred_paths = {
        "life.vvault.identity.prompt": (
            "identity/prompt.json",
            "identity/prompt.txt",
            "config/metadata.json",
        ),
        "life.vvault.identity.definition": (
            "identity/definition.json",
            "identity/definition.txt",
            "identity/prompt.json",
            "identity/prompt.txt",
            "config/metadata.json",
        ),
        "life.vvault.identity.conditioning": (
            "identity/conditioning.txt",
            "identity/prompt.json",
            "identity/prompt.txt",
            "identity/definition.json",
            "identity/definition.txt",
        ),
        "life.vvault.identity.physical-features": (
            "identity/physical_features.json",
            "memup/{id}.capsule",
            "identity/prompt.json",
        ),
        "life.vvault.identity.voice-profile": (
            "identity/voice.json",
            "identity/voice.md",
            "config/voice.md",
            "config/metadata.json",
        ),
    }.get(artifact_id, ())
    resolved = tuple(path.replace("{id}", instance_id) for path in preferred_paths)
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        row_instance, relative = relative_instance_path(row)
        if row_instance != instance_id or relative not in resolved:
            continue
        matches.append((
            resolved.index(relative),
            -_projection_rank(row, instance_id=instance_id, relative_path=relative),
            row,
        ))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in matches]


def _transform_operations(
    rows: list[dict[str, Any]],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    registry = load_registry()
    specs = {item["artifact_id"]: item for item in registry["artifacts"]}
    target_codes = {"schema_violation", "invalid_json", "missing_required_artifact"}
    targets = {
        (issue["instance_id"], issue["artifact_id"])
        for issue in report["issues"]
        if issue["code"] in target_codes and issue.get("artifact_id")
    }
    for issue in report["issues"]:
        if issue["code"] == "legacy_alias" and not issue.get("canonical_present"):
            targets.add((issue["instance_id"], issue["artifact_id"]))

    operations = []
    for instance_id, artifact_id in sorted(targets):
        artifact = specs.get(artifact_id)
        if not artifact:
            continue
        # Binary artifacts are hash-preserving references/copies. They must
        # never pass through the JSON/text transformation pipeline.
        if not artifact.get("schema_id") and artifact_id != "life.vvault.identity.conditioning":
            continue
        candidates = _candidate_rows_for_target(
            rows,
            instance_id=instance_id,
            artifact=artifact,
        )
        ranked = sorted(
            candidates,
            key=lambda item: _projection_rank(
                item[1],
                instance_id=instance_id,
                relative_path=item[0],
            ),
            reverse=True,
        )
        source_rows = [row for _, row in ranked]
        for row in _related_source_rows(
            rows,
            instance_id=instance_id,
            artifact_id=artifact_id,
        ):
            if str(row.get("id") or "") not in {
                str(existing.get("id") or "") for existing in source_rows
            }:
                source_rows.append(row)
        if not source_rows:
            continue
        owner_ids = {str(row.get("user_id") or "") for row in source_rows if row.get("user_id")}
        if len(owner_ids) != 1:
            continue
        primary = source_rows[0]
        after_path = render_path(artifact["canonical_path"], instance_id)
        before_instance, before_path = relative_instance_path(primary)
        preview_content, preview_content_type = transform_content(
            artifact_id,
            instance_id,
            source_rows,
        )
        schema_id = artifact.get("schema_id")
        schema = load_schemas().get(schema_id) if schema_id else None
        if schema:
            try:
                preview_document = json.loads(preview_content)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"transformation for {instance_id} {artifact_id} did not emit JSON"
                ) from exc
            validation_errors = validate_json_document(preview_document, schema)
            if validation_errors:
                raise ValueError(
                    f"transformation for {instance_id} {artifact_id} is invalid: "
                    + "; ".join(validation_errors)
                )
        expected_after_sha256 = hashlib.sha256(
            preview_content.encode("utf-8")
        ).hexdigest()
        operation = {
            "operation": "transform_then_insert",
            "record_id": str(primary.get("id") or ""),
            "source_record_ids": [str(row.get("id") or "") for row in source_rows],
            "source_hashes": {
                str(row.get("id") or ""): str(row.get("sha256") or "")
                for row in source_rows
            },
            "instance_id": instance_id,
            "artifact_id": artifact_id,
            "before_path": before_path or "",
            "after_path": after_path,
            "before_sha256": str(primary.get("sha256") or ""),
            "before_schema_version": None,
            "after_schema_version": artifact.get("schema_version"),
            "after_content_type": preview_content_type,
            "expected_after_sha256": expected_after_sha256,
            "destination_bucket": CANONICAL_BUCKET,
            "preconditions": [
                "authenticated OVVAULTS transaction",
                "all source hashes unchanged",
                "all source owners identical",
                "no canonical-v1 destination with a different hash",
            ],
            "rollback": "remove only the newly inserted canonical-v1 projection using its receipt id; every source remains untouched",
        }
        operation["plan_sha256"] = hashlib.sha256(
            json.dumps(operation, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        operations.append(operation)
    return operations


def plan_migration(
    report: dict[str, Any],
    rows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    collision_keys = {
        (issue["instance_id"], issue["artifact_id"])
        for issue in report["issues"]
        if issue["severity"] == "collision"
    }
    for issue in report["issues"]:
        if issue["severity"] == "collision":
            quarantined.append(
                {
                    "instance_id": issue["instance_id"],
                    "artifact_id": issue["artifact_id"],
                    "reason": issue["code"],
                    "records": issue.get("records", []),
                    "requires_handler_approval": True,
                }
            )
            continue
        if (issue["instance_id"], issue["artifact_id"]) in collision_keys:
            continue
        if issue["code"] in {"legacy_alias", "incorrect_casing"}:
            if issue.get("canonical_present"):
                continue
            record = issue["record"]
            source_sha256 = str(record.get("sha256") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
                skipped.append(
                    {
                        "instance_id": issue["instance_id"],
                        "artifact_id": issue["artifact_id"],
                        "record_id": record["id"],
                        "before_path": issue["actual_path"],
                        "after_path": issue["canonical_path"],
                        "reason": "source content hash is unavailable; no bytes can be verified or copied",
                    }
                )
                continue
            operation = {
                "operation": "copy_then_verify",
                "record_id": record["id"],
                "instance_id": issue["instance_id"],
                "artifact_id": issue["artifact_id"],
                "before_path": issue["actual_path"],
                "after_path": issue["canonical_path"],
                "before_sha256": record["sha256"],
                "preconditions": [
                    "authenticated OVVAULTS transaction",
                    "destination absent",
                    "source hash unchanged",
                    "ownership unchanged",
                ],
                "rollback": "remove only the newly inserted projection using its receipt id; source remains untouched",
            }
            operation["plan_sha256"] = hashlib.sha256(
                json.dumps(operation, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            operations.append(operation)
    if rows is not None:
        transformed = _transform_operations(list(rows), report)
        transformed_keys = {
            (item["instance_id"], item["artifact_id"]) for item in transformed
        }
        operations = [
            item for item in operations
            if (item["instance_id"], item["artifact_id"]) not in transformed_keys
        ]
        skipped = [
            item for item in skipped
            if (item["instance_id"], item["artifact_id"]) not in transformed_keys
        ]
        operations.extend(transformed)
        operations.sort(key=lambda item: (item["instance_id"], item["artifact_id"]))
    return {
        "migration_id": "life.vvault.canonical-layout.v1",
        "contract_version": "1.0.0",
        "mode": "dry-run",
        "source_report_generated_at": report["generated_at"],
        "operation_count": len(operations),
        "collision_count": len(quarantined),
        "skipped_count": len(skipped),
        "operations": operations,
        "quarantine": quarantined,
        "skipped": skipped,
    }


def reviewed_migration_map(report: dict[str, Any]) -> dict[str, Any]:
    registry = load_registry()
    discovered = {
        (
            issue.get("artifact_id"),
            issue.get("instance_id"),
            issue.get("actual_path"),
            issue.get("canonical_path"),
        )
        for issue in report["issues"]
        if issue["code"] in {"legacy_alias", "incorrect_casing"}
    }
    entries = []
    for artifact in registry["artifacts"]:
        for alias in artifact["legacy_aliases"]:
            matching = sorted(
                {
                    (instance_id, actual, canonical)
                    for artifact_id, instance_id, actual, canonical in discovered
                    if artifact_id == artifact["artifact_id"]
                    and actual
                    and canonical
                }
            )
            entries.append(
                {
                    "artifact_id": artifact["artifact_id"],
                    "legacy_alias_template": alias,
                    "canonical_path_template": artifact["canonical_path"],
                    "migration_rule": artifact["migration_rule"],
                    "collision_behavior": artifact["collision_behavior"],
                    "discovered_instances": [
                        {
                            "instance_id": instance_id,
                            "actual_path": actual,
                            "canonical_path": canonical,
                        }
                        for instance_id, actual, canonical in matching
                        if path_matches_template(actual.casefold(), alias.casefold())
                    ],
                    "review_status": "contract-reviewed",
                }
            )
    return {
        "map_id": "life.vvault.canonical-layout.legacy-map",
        "contract_version": "1.0.0",
        "generated_at": report["generated_at"],
        "entry_count": len(entries),
        "entries": entries,
    }


def unresolved_conflicts(report: dict[str, Any]) -> dict[str, Any]:
    conflicts = [
        {
            "instance_id": issue["instance_id"],
            "artifact_id": issue["artifact_id"],
            "reason": issue["code"],
            "canonical_path": issue.get("canonical_path"),
            "candidate_records": issue.get("records", []),
            "decision_required": (
                "Select or reconcile a canonical source using content and provenance evidence; "
                "no automatic winner is permitted."
            ),
        }
        for issue in report["issues"]
        if issue["severity"] == "collision"
    ]
    return {
        "list_id": "life.vvault.canonical-layout.unresolved-conflicts",
        "contract_version": "1.0.0",
        "generated_at": report["generated_at"],
        "requires_handler": "Devon",
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }


def _safe_provenance(metadata: Any) -> dict[str, Any]:
    value = metadata if isinstance(metadata, dict) else {}
    allowed = {
        "source",
        "provider",
        "folder",
        "construct_id",
        "schema_id",
        "schema_version",
        "source_table",
        "source_row_id",
        "materialized_at",
        "canonical_contract",
        "identity_projection",
        "history_status",
    }
    return {key: value[key] for key in sorted(allowed & set(value))}


def _content_summary(content: Any, *, schema: dict[str, Any] | None) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    encoded = text.encode("utf-8")
    summary: dict[str, Any] = {
        "actual_sha256": hashlib.sha256(encoded).hexdigest(),
        "byte_length": len(encoded),
        "line_count": len(text.splitlines()),
    }
    try:
        document = content if isinstance(content, (dict, list)) else json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        summary["serialization"] = "text"
        return summary
    summary["serialization"] = "json"
    if isinstance(document, dict):
        summary["top_level_keys"] = sorted(document)
        summary["declared_schema_id"] = document.get("schema_id")
        summary["declared_schema_version"] = (
            document.get("schema_version")
            or document.get("capsule_version")
            or (document.get("metadata") or {}).get("capsule_version")
        )
        if schema:
            validation_errors = validate_json_document(document, schema)
            summary["target_schema_valid"] = not validation_errors
            summary["target_schema_error_count"] = len(validation_errors)
        if isinstance(document.get("metadata"), dict):
            capsule_metadata = document["metadata"]
            summary["capsule_uuid"] = capsule_metadata.get("capsule_uuid")
            summary["lineage_uuid"] = capsule_metadata.get("lineage_uuid")
        source_manifest = document.get("source_manifest")
        if isinstance(source_manifest, dict) and isinstance(source_manifest.get("sources"), list):
            summary["source_manifest_count"] = len(source_manifest["sources"])
        sessions = document.get("sessions")
        if isinstance(sessions, list):
            summary["session_count"] = len(sessions)
    return summary


def _pairwise_content_relations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations = []
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1:]:
            left_text = (
                left.get("content")
                if isinstance(left.get("content"), str)
                else json.dumps(left.get("content"), sort_keys=True, default=str)
            )
            right_text = (
                right.get("content")
                if isinstance(right.get("content"), str)
                else json.dumps(right.get("content"), sort_keys=True, default=str)
            )
            left_lines = {line.strip() for line in left_text.splitlines() if line.strip()}
            right_lines = {line.strip() for line in right_text.splitlines() if line.strip()}
            union = left_lines | right_lines
            relation: dict[str, Any] = {
                "left_record_id": str(left["id"]),
                "right_record_id": str(right["id"]),
                "exact_content": left_text == right_text,
                "left_is_substring_of_right": bool(left_text) and left_text in right_text,
                "right_is_substring_of_left": bool(right_text) and right_text in left_text,
                "nonempty_line_jaccard": (
                    round(len(left_lines & right_lines) / len(union), 6)
                    if union else 1.0
                ),
            }
            try:
                left_json = json.loads(left_text)
                right_json = json.loads(right_text)
            except (TypeError, ValueError, json.JSONDecodeError):
                left_json = right_json = None
            if isinstance(left_json, dict) and isinstance(right_json, dict):
                left_keys = set(left_json)
                right_keys = set(right_json)
                relation["json_key_overlap"] = sorted(left_keys & right_keys)
                relation["left_only_json_keys"] = sorted(left_keys - right_keys)
                relation["right_only_json_keys"] = sorted(right_keys - left_keys)
                left_sessions = left_json.get("sessions")
                right_sessions = right_json.get("sessions")
                if isinstance(left_sessions, list) and isinstance(right_sessions, list):
                    left_session_hashes = {
                        hashlib.sha256(
                            json.dumps(item, sort_keys=True, default=str).encode()
                        ).hexdigest()
                        for item in left_sessions
                    }
                    right_session_hashes = {
                        hashlib.sha256(
                            json.dumps(item, sort_keys=True, default=str).encode()
                        ).hexdigest()
                        for item in right_sessions
                    }
                    relation["shared_session_count"] = len(
                        left_session_hashes & right_session_hashes
                    )
                    relation["left_unique_session_count"] = len(
                        left_session_hashes - right_session_hashes
                    )
                    relation["right_unique_session_count"] = len(
                        right_session_hashes - left_session_hashes
                    )
            relations.append(relation)
    return relations


def analyze_conflicts(report: dict[str, Any]) -> dict[str, Any]:
    """Compare collision evidence without returning canonical content."""
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for issue in report["issues"]:
        if issue["severity"] != "collision":
            continue
        key = (issue["instance_id"], issue["artifact_id"])
        grouped[key].update(
            str(record["id"])
            for record in issue.get("records", [])
            if record.get("id")
        )
    record_ids = sorted({record_id for values in grouped.values() for record_id in values})
    rows = chatty_body_service._rows(
        """
        SELECT id::text AS id, user_id::text AS user_id, construct_id,
               filename, object_key, storage_path, content_type, file_type,
               sha256, created_at, updated_at, materialized_at,
               source_table, source_row_id, source_filename,
               source_storage_path, metadata, content
        FROM ovvaults.vault_files
        WHERE id = ANY(%s::uuid[])
        ORDER BY construct_id, coalesce(updated_at, created_at), id
        """,
        (record_ids,),
    ) if record_ids else []
    rows_by_id = {str(row["id"]): dict(row) for row in rows}
    schemas = load_schemas()
    comparisons = []
    for (instance_id, artifact_id), ids in sorted(grouped.items()):
        spec = artifact_spec(artifact_id) or {}
        schema = schemas.get(spec.get("schema_id"))
        candidates = []
        candidate_rows = []
        owners = set()
        actual_hashes = set()
        for record_id in sorted(ids):
            row = rows_by_id.get(record_id)
            if not row:
                candidates.append({"record_id": record_id, "status": "missing_since_audit"})
                continue
            owners.add(str(row.get("user_id") or ""))
            candidate_rows.append(row)
            summary = _content_summary(row.get("content"), schema=schema)
            actual_hashes.add(summary["actual_sha256"])
            path_instance, relative_path = relative_instance_path(row)
            candidates.append({
                "record_id": record_id,
                "status": "present",
                "owner_user_id": str(row.get("user_id") or ""),
                "construct_id": str(row.get("construct_id") or ""),
                "path_instance_id": path_instance,
                "relative_path": relative_path,
                "stored_sha256": str(row.get("sha256") or ""),
                "stored_hash_matches_content": (
                    not row.get("sha256")
                    or str(row.get("sha256")) == summary["actual_sha256"]
                ),
                "created_at": str(row.get("created_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
                "materialized_at": str(row.get("materialized_at") or ""),
                "source_table": str(row.get("source_table") or ""),
                "source_row_id": str(row.get("source_row_id") or ""),
                "source_filename": str(row.get("source_filename") or ""),
                "source_storage_path": str(row.get("source_storage_path") or ""),
                "provenance": _safe_provenance(row.get("metadata")),
                "content_summary": summary,
            })
        semantic_type = spec.get("semantic_type")
        if semantic_type == "product-transcript":
            recommendation = "append_preserving_transcript_reconciliation"
            rationale = (
                "Preserve both transcript records and reconcile messages/order through the "
                "transcript API; never choose by timestamp or overwrite either record."
            )
        elif semantic_type == "canonical-life-capsule":
            recommendation = "preserve_both_capsule_lineages_pending_promotion"
            rationale = (
                "Preserve both capsule records and histories; Devon must approve which lineage "
                "the canonical resolver promotes after source-manifest review."
            )
        elif len(actual_hashes) == 1 and len(candidates) > 1:
            recommendation = "deduplicate_identical_content_after_handler_approval"
            rationale = (
                "Content hashes are identical; retain all history and collapse only redundant "
                "canonical projections with an approved receipt."
            )
        else:
            valid = [
                item
                for item in candidates
                if item.get("content_summary", {}).get("target_schema_valid") is True
            ]
            recommendation = (
                "prefer_schema_valid_candidate_pending_handler"
                if len(valid) == 1
                else "manual_field_reconciliation_required"
            )
            rationale = (
                "A single candidate validates against the target schema, but it remains only "
                "a recommendation until Devon confirms provenance and field ownership."
                if len(valid) == 1
                else "Candidates differ and cannot be selected safely without Devon's field-level decision."
            )
        comparisons.append({
            "instance_id": instance_id,
            "artifact_id": artifact_id,
            "semantic_type": semantic_type,
            "candidate_count": len(candidates),
            "owner_consistent": len(owners) <= 1,
            "distinct_actual_content_hashes": len(actual_hashes),
            "recommendation": recommendation,
            "rationale": rationale,
            "automatic_selection": None,
            "requires_handler_decision": True,
            "pairwise_relations": _pairwise_content_relations(candidate_rows),
            "candidates": candidates,
        })
    return {
        "analysis_id": "life.vvault.canonical-layout.conflict-analysis",
        "contract_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analyst_identity": "Zenith of Codex",
        "authority": load_registry()["canonical_authority"],
        "mode": "read-only",
        "content_included": False,
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
    }


class MigrationCollision(RuntimeError):
    """Raised when apply discovers evidence that requires handler review."""


@dataclass
class CanonicalMigrationService:
    """Authenticated, transactional, idempotent copy-and-verify migration."""

    connect: Any = chatty_body_service._connect

    def apply_operation(self, operation: dict[str, Any], *, actor: str) -> dict[str, Any]:
        operation_id = str(operation["plan_sha256"])
        instance_id = str(operation["instance_id"])
        destination_path = f"instances/{instance_id}/{operation['after_path']}"
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"canonical-artifact:{instance_id}:{operation['artifact_id']}",),
                )
                cur.execute(
                    """
                    SELECT receipt
                    FROM ovvaults.canonical_artifact_migration_receipts
                    WHERE operation_id = %s
                    """,
                    (operation_id,),
                )
                previous = cur.fetchone()
                if previous:
                    receipt = dict(previous["receipt"])
                    receipt["result"] = "already_applied"
                    conn.commit()
                    return receipt

                cur.execute(
                    """
                    SELECT *
                    FROM ovvaults.vault_files
                    WHERE id = %s
                    FOR SHARE
                    """,
                    (operation["record_id"],),
                )
                source = cur.fetchone()
                if not source:
                    raise RuntimeError(f"source record disappeared: {operation['record_id']}")
                source = dict(source)
                if str(source.get("construct_id") or "") != instance_id:
                    raise RuntimeError("source construct ownership changed after dry-run")
                source_hash = str(source.get("sha256") or "")
                if source_hash != str(operation.get("before_sha256") or ""):
                    raise RuntimeError("source hash changed after dry-run")

                is_transform = operation.get("operation") == "transform_then_insert"
                source_rows = [source]
                if is_transform:
                    source_ids = [
                        value
                        for value in operation.get("source_record_ids", [])
                        if value != str(source["id"])
                    ]
                    if source_ids:
                        cur.execute(
                            """
                            SELECT *
                            FROM ovvaults.vault_files
                            WHERE id = ANY(%s::uuid[])
                            ORDER BY coalesce(updated_at, created_at) DESC, id
                            FOR SHARE
                            """,
                            (source_ids,),
                        )
                        fetched = {
                            str(row["id"]): dict(row)
                            for row in cur.fetchall()
                        }
                        source_rows.extend(
                            fetched[source_id]
                            for source_id in source_ids
                            if source_id in fetched
                        )
                    expected_ids = set(operation.get("source_record_ids", []))
                    actual_ids = {str(row["id"]) for row in source_rows}
                    if actual_ids != expected_ids:
                        raise RuntimeError("one or more transformation sources disappeared")
                    expected_hashes = operation.get("source_hashes", {})
                    for row in source_rows:
                        record_id = str(row["id"])
                        if str(row.get("user_id") or "") != str(source["user_id"]):
                            raise RuntimeError("transformation source ownership changed")
                        if str(row.get("sha256") or "") != str(expected_hashes.get(record_id) or ""):
                            raise RuntimeError("transformation source hash changed after dry-run")
                    transformed_content, transformed_content_type = transform_content(
                        str(operation["artifact_id"]),
                        instance_id,
                        source_rows,
                    )
                    transformed_hash = hashlib.sha256(
                        transformed_content.encode("utf-8")
                    ).hexdigest()
                    if transformed_hash != str(
                        operation.get("expected_after_sha256") or ""
                    ):
                        raise RuntimeError(
                            "deterministic transformation output changed after dry-run"
                        )
                    destination_bucket = str(
                        operation.get("destination_bucket") or CANONICAL_BUCKET
                    )
                    destination_object_key = (
                        f"users/{source['user_id']}/{destination_path}"
                    )
                    cur.execute(
                        """
                        SELECT *
                        FROM ovvaults.vault_files
                        WHERE user_id = %s
                          AND construct_id = %s
                          AND bucket = %s
                          AND object_key = %s
                        ORDER BY coalesce(updated_at, created_at) DESC
                        FOR SHARE
                        """,
                        (
                            source["user_id"],
                            instance_id,
                            destination_bucket,
                            destination_object_key,
                        ),
                    )
                else:
                    transformed_content = source.get("content")
                    transformed_content_type = source.get("content_type")
                    transformed_hash = source_hash
                    destination_bucket = source.get("bucket")
                    # Keep the logical projection key unique while recording
                    # the immutable backing object below. Readers follow the
                    # receipt-backed source_object_key; no binary bytes are
                    # copied, transcoded, or overwritten.
                    source_object_key = (
                        source.get("object_key") or source.get("storage_path")
                    )
                    destination_object_key = (
                        f"{source_object_key}#canonical:{operation_id}"
                    )
                    cur.execute(
                        """
                        SELECT *
                        FROM ovvaults.vault_files
                        WHERE user_id = %s
                          AND construct_id = %s
                          AND lower(coalesce(storage_path, filename, '')) = lower(%s)
                        ORDER BY coalesce(updated_at, created_at) DESC
                        FOR SHARE
                        """,
                        (source["user_id"], instance_id, destination_path),
                    )
                destinations = [dict(row) for row in cur.fetchall()]
                distinct_hashes = {
                    str(row.get("sha256") or "") for row in destinations
                }
                if destinations and distinct_hashes != {transformed_hash}:
                    evidence = {
                        "operation": operation,
                        "source_record_id": str(source["id"]),
                        "destination_record_ids": [str(row["id"]) for row in destinations],
                        "destination_hashes": sorted(distinct_hashes),
                    }
                    cur.execute(
                        """
                        INSERT INTO ovvaults.canonical_artifact_quarantine (
                            migration_id, contract_version, owner_user_id,
                            instance_id, artifact_id, reason,
                            candidate_record_ids, evidence
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s::uuid[], %s::jsonb)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            "life.vvault.canonical-layout.v1",
                            "1.0.0",
                            source["user_id"],
                            instance_id,
                            operation["artifact_id"],
                            "apply_time_collision",
                            [str(source["id"]), *[str(row["id"]) for row in destinations]],
                            json.dumps(evidence),
                        ),
                    )
                    conn.commit()
                    raise MigrationCollision(
                        f"collision quarantined for {instance_id} {operation['artifact_id']}"
                    )

                created_destination = not destinations
                if destinations:
                    destination = destinations[0]
                    result = "already_applied"
                else:
                    metadata = source.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata = {
                        **metadata,
                        "canonical_contract": {
                            "contract_id": "life.vvault.data-layout",
                            "contract_version": "1.0.0",
                            "source_record_id": str(source["id"]),
                            "migration_operation_id": operation_id,
                            "source_bucket": source.get("bucket"),
                            "source_object_key": source.get("object_key"),
                        },
                    }
                    cur.execute(
                        """
                        INSERT INTO ovvaults.vault_files (
                            user_id, bucket, object_key, filename, content_type,
                            size_bytes, sha256, created_at, content, metadata,
                            construct_id, storage_path, file_type, source_table,
                            source_row_id, source_filename, source_storage_path,
                            materialized_at, is_system, updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s::jsonb,
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            now(), %s, now()
                        )
                        RETURNING *
                        """,
                        (
                            source["user_id"],
                            destination_bucket,
                            destination_object_key,
                            destination_path,
                            transformed_content_type,
                            len(transformed_content.encode("utf-8")) if isinstance(transformed_content, str) else source.get("size_bytes"),
                            transformed_hash,
                            source.get("created_at"),
                            transformed_content,
                            json.dumps(metadata),
                            instance_id,
                            destination_path,
                            source.get("file_type"),
                            "ovvaults.vault_files",
                            str(source["id"]),
                            source.get("filename"),
                            source.get("storage_path"),
                            source.get("is_system", False),
                        ),
                    )
                    destination = dict(cur.fetchone())
                    if str(destination.get("sha256") or "") != transformed_hash:
                        raise RuntimeError("destination hash verification failed")
                    result = "applied"

                receipt = {
                    "operation_id": operation_id,
                    "migration_id": "life.vvault.canonical-layout.v1",
                    "contract_version": "1.0.0",
                    "actor": actor,
                    "source_record_id": str(source["id"]),
                    "destination_record_id": str(destination["id"]),
                    "owner_user_id": str(source["user_id"]),
                    "instance_id": instance_id,
                    "artifact_id": operation["artifact_id"],
                    "before_path": operation["before_path"],
                    "after_path": operation["after_path"],
                    "before_sha256": source_hash,
                    "after_sha256": str(destination.get("sha256") or ""),
                    "before_schema_version": operation.get("before_schema_version"),
                    "after_schema_version": operation.get("after_schema_version"),
                    "created_destination": created_destination,
                    "result": result,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                }
                cur.execute(
                    """
                    INSERT INTO ovvaults.canonical_artifact_migration_receipts (
                        operation_id, migration_id, contract_version, actor,
                        source_record_id, destination_record_id, owner_user_id,
                        instance_id, artifact_id, before_path, after_path,
                        before_sha256, after_sha256, before_schema_version,
                        after_schema_version, result, receipt
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s::jsonb
                    )
                    """,
                    (
                        operation_id,
                        receipt["migration_id"],
                        receipt["contract_version"],
                        actor,
                        receipt["source_record_id"],
                        receipt["destination_record_id"],
                        receipt["owner_user_id"],
                        instance_id,
                        receipt["artifact_id"],
                        receipt["before_path"],
                        receipt["after_path"],
                        source_hash,
                        receipt["after_sha256"],
                        receipt["before_schema_version"],
                        receipt["after_schema_version"],
                        result,
                        json.dumps(receipt),
                    ),
                )
            conn.commit()
        return receipt

    def rollback_operation(
        self,
        operation_id: str,
        *,
        actor: str,
        explicit_handler_approval: bool,
    ) -> dict[str, Any]:
        if not explicit_handler_approval:
            raise PermissionError("explicit handler approval is required for rollback")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM ovvaults.canonical_artifact_migration_receipts
                    WHERE operation_id = %s
                    FOR UPDATE
                    """,
                    (operation_id,),
                )
                stored = cur.fetchone()
                if not stored:
                    raise KeyError(operation_id)
                stored = dict(stored)
                receipt = dict(stored["receipt"])
                if stored.get("result") == "rolled_back":
                    return dict(stored.get("rollback_receipt") or {})
                if not receipt.get("created_destination"):
                    raise RuntimeError("rollback refused: migration did not create destination")
                cur.execute(
                    """
                    DELETE FROM ovvaults.vault_files
                    WHERE id = %s
                      AND sha256 IS NOT DISTINCT FROM %s
                      AND storage_path = %s
                    RETURNING id::text AS id
                    """,
                    (
                        stored["destination_record_id"],
                        stored.get("after_sha256"),
                        f"instances/{stored['instance_id']}/{stored['after_path']}",
                    ),
                )
                deleted = cur.fetchone()
                if not deleted:
                    raise RuntimeError("rollback preconditions failed; destination changed")
                rollback = {
                    "operation_id": operation_id,
                    "rolled_back_by": actor,
                    "rolled_back_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_destination_record_id": str(deleted["id"]),
                    "source_record_preserved": True,
                }
                cur.execute(
                    """
                    UPDATE ovvaults.canonical_artifact_migration_receipts
                    SET result = 'rolled_back',
                        rolled_back_at = now(),
                        rolled_back_by = %s,
                        rollback_receipt = %s::jsonb
                    WHERE operation_id = %s
                    """,
                    (actor, json.dumps(rollback), operation_id),
                )
            conn.commit()
        return rollback


def artifact_spec(artifact_id: str) -> dict[str, Any] | None:
    return next(
        (
            artifact
            for artifact in load_registry()["artifacts"]
            if artifact["artifact_id"] == artifact_id
        ),
        None,
    )


def resolve_artifact(
    *,
    artifact_id: str,
    instance_id: str,
    owner_user_id: str,
) -> tuple[dict[str, Any], int]:
    spec = artifact_spec(artifact_id)
    canonical_id = canonical_instance_id(instance_id)
    if not spec:
        return {"success": False, "error": "unknown artifact_id"}, 404
    if not re.fullmatch(
        load_registry()["identifier_patterns"]["canonical-instance-id"],
        canonical_id,
    ):
        return {"success": False, "error": "invalid canonical instance_id"}, 400
    relative_path = render_path(spec["canonical_path"], canonical_id)
    storage_path = f"instances/{canonical_id}/{relative_path}"
    rows = chatty_body_service._rows(
        """
        SELECT id::text AS id, user_id::text AS user_id, construct_id,
               bucket, object_key, filename, storage_path, content_type, file_type, sha256,
               created_at, updated_at, metadata
        FROM ovvaults.vault_files
        WHERE user_id = %s
          AND construct_id = %s
          AND coalesce(filename, storage_path, '') = %s
        ORDER BY coalesce(updated_at, created_at) DESC
        """,
        (owner_user_id, canonical_id, storage_path),
    )
    if not rows:
        return {
            "success": False,
            "canonical": True,
            "artifact_id": artifact_id,
            "instance_id": canonical_id,
            "canonical_path": relative_path,
            "error": "canonical artifact not found",
        }, 404
    hashes = {str(row.get("sha256") or "") for row in rows}
    ranked_rows = sorted(
        [
            (
                _projection_rank(
                    row,
                    instance_id=canonical_id,
                    relative_path=relative_path,
                ),
                row,
            )
            for row in rows
        ],
        key=lambda item: item[0],
    )
    if len(ranked_rows) > 1 and ranked_rows[-1][0] > ranked_rows[-2][0]:
        row = ranked_rows[-1][1]
        historical_record_ids = [
            candidate["id"] for candidate in rows if candidate["id"] != row["id"]
        ]
    elif len(rows) > 1 or len(hashes) > 1:
        return {
            "success": False,
            "canonical": False,
            "artifact_id": artifact_id,
            "instance_id": canonical_id,
            "canonical_path": relative_path,
            "error": "canonical artifact collision",
            "candidate_record_ids": [row["id"] for row in rows],
            "candidate_hashes": sorted(hashes),
            "requires_handler_decision": True,
        }, 409
    else:
        row = rows[0]
        historical_record_ids = []
    return {
        "success": True,
        "canonical": True,
        "authority": "vvault_body",
        "schema": "ovvaults",
        "storage_owner": "ovvaults.vault_files",
        "contract_id": "life.vvault.data-layout",
        "contract_version": "1.0.0",
        "artifact_id": artifact_id,
        "semantic_type": spec["semantic_type"],
        "instance_id": canonical_id,
        "owner_user_id": row["user_id"],
        "canonical_path": relative_path,
        "storage_path": storage_path,
        "record_id": row["id"],
        "historical_projection_record_ids": historical_record_ids,
        "sha256": row.get("sha256"),
        "schema_id": spec.get("schema_id"),
        "schema_version": spec.get("schema_version"),
        "mime_type": spec["mime_type"],
        "updated_at": str(row.get("updated_at") or row.get("created_at") or ""),
    }, 200


def resolve_manifest(*, instance_id: str, owner_user_id: str) -> tuple[dict[str, Any], int]:
    entries = []
    conflicts = []
    for spec in load_registry()["artifacts"]:
        resolved, status = resolve_artifact(
            artifact_id=spec["artifact_id"],
            instance_id=instance_id,
            owner_user_id=owner_user_id,
        )
        if status == 409:
            conflicts.append(resolved)
        entries.append({"status": status, **resolved})
    return {
        "success": not conflicts,
        "canonical": not conflicts,
        "authority": "vvault_body",
        "schema": "ovvaults",
        "contract_id": "life.vvault.data-layout",
        "contract_version": "1.0.0",
        "instance_id": canonical_instance_id(instance_id),
        "owner_user_id": owner_user_id,
        "artifacts": entries,
        "conflict_count": len(conflicts),
    }, 409 if conflicts else 200


def authenticated_rows(owner_user_id: str) -> list[dict[str, Any]]:
    if not owner_user_id:
        raise ValueError("owner_user_id is required")
    return chatty_body_service._rows(
        """
        SELECT id, user_id, construct_id, bucket, filename, object_key, storage_path,
               file_type, content_type, size_bytes, sha256, created_at, updated_at,
               metadata, is_system, source_table, source_row_id,
               source_filename, source_storage_path,
               CASE
                 WHEN lower(coalesce(storage_path, object_key, filename, ''))
                      ~ '\\.(json|capsule)$'
                   OR lower(coalesce(storage_path, object_key, filename, ''))
                      ~ '/(definition|prompt|conditioning)\\.txt($|#)'
                   OR lower(coalesce(storage_path, object_key, filename, ''))
                      ~ '/voice\\.md($|#)'
                 THEN content
                 ELSE NULL
               END AS content
        FROM ovvaults.vault_files
        WHERE user_id = %s
        ORDER BY construct_id, created_at, id
        """,
        (owner_user_id,),
    )


def administrative_rows() -> list[dict[str, Any]]:
    """Return all owners' rows for explicit administrative migration tooling only."""
    return chatty_body_service._rows(
        """
        SELECT id, user_id, construct_id, bucket, filename, object_key, storage_path,
               file_type, content_type, size_bytes, sha256, created_at, updated_at,
               metadata, is_system, source_table, source_row_id,
               source_filename, source_storage_path,
               CASE
                 WHEN lower(coalesce(storage_path, object_key, filename, ''))
                      ~ '\\.(json|capsule)$'
                   OR lower(coalesce(storage_path, object_key, filename, ''))
                      ~ '/(definition|prompt|conditioning)\\.txt($|#)'
                   OR lower(coalesce(storage_path, object_key, filename, ''))
                      ~ '/voice\\.md($|#)'
                 THEN content
                 ELSE NULL
               END AS content
        FROM ovvaults.vault_files
        ORDER BY user_id, construct_id, created_at, id
        """
    )


def migration_receipts(
    migration_id: str = "life.vvault.canonical-layout.v1",
) -> dict[str, Any]:
    rows = chatty_body_service._rows(
        """
        SELECT operation_id, migration_id, contract_version, actor,
               source_record_id::text AS source_record_id,
               destination_record_id::text AS destination_record_id,
               owner_user_id::text AS owner_user_id,
               instance_id, artifact_id, before_path, after_path,
               before_sha256, after_sha256, before_schema_version,
               after_schema_version, result, applied_at, rolled_back_at,
               rolled_back_by, rollback_receipt, receipt
        FROM ovvaults.canonical_artifact_migration_receipts
        WHERE migration_id = %s
        ORDER BY applied_at, operation_id
        """,
        (migration_id,),
    )
    normalized = [
        {
            key: (
                value.isoformat()
                if hasattr(value, "isoformat")
                else value
            )
            for key, value in row.items()
        }
        for row in rows
    ]
    return {
        "evidence_id": "life.vvault.canonical-layout.migration-receipts",
        "contract_version": "1.0.0",
        "migration_id": migration_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "receipt_count": len(normalized),
        "receipts": normalized,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit canonical VVAULT artifact layout")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--migration-plan", type=Path)
    parser.add_argument("--migration-map", type=Path)
    parser.add_argument("--unresolved-conflicts", type=Path)
    parser.add_argument("--conflict-analysis", type=Path)
    parser.add_argument("--receipts", type=Path)
    args = parser.parse_args()
    try:
        rows = administrative_rows()
        report = audit_rows(rows)
    finally:
        chatty_body_service.close_body_database_pool()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.migration_plan:
        plan = plan_migration(report, rows)
        args.migration_plan.parent.mkdir(parents=True, exist_ok=True)
        args.migration_plan.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    if args.migration_map:
        migration_map = reviewed_migration_map(report)
        args.migration_map.parent.mkdir(parents=True, exist_ok=True)
        args.migration_map.write_text(
            json.dumps(migration_map, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.unresolved_conflicts:
        conflicts = unresolved_conflicts(report)
        args.unresolved_conflicts.parent.mkdir(parents=True, exist_ok=True)
        args.unresolved_conflicts.write_text(
            json.dumps(conflicts, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.conflict_analysis:
        try:
            analysis = analyze_conflicts(report)
        finally:
            chatty_body_service.close_body_database_pool()
        args.conflict_analysis.parent.mkdir(parents=True, exist_ok=True)
        args.conflict_analysis.write_text(
            json.dumps(analysis, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.receipts:
        try:
            receipts = migration_receipts()
        finally:
            chatty_body_service.close_body_database_pool()
        args.receipts.parent.mkdir(parents=True, exist_ok=True)
        args.receipts.write_text(
            json.dumps(receipts, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if report["conforms"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
