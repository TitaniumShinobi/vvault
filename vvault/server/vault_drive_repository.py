"""Owner-scoped, receipt-backed first-class folder hierarchy for VVAULT."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

try:
    import chatty_body_service
except ImportError:
    from vvault.server import chatty_body_service


PROTECTED_ROOTS = {"identity", "config", "chatty", "assets", "documents", "memup"}
PROTECTED_FILE_ROOTS = {"identity", "config", "chatty", "memup"}
TRANSCRIPT_PROVIDER_ROOTS = {"chatgpt", "character.ai", "codex", "github-copilot"}
_FOLDER_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,255}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(row: Any) -> dict[str, Any]:
    result = dict(row or {})
    for key, value in list(result.items()):
        if isinstance(value, bytes):
            result[key] = value.decode("utf-8")
    for key in ("id", "owner_user_id", "parent_node_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    for key in ("created_at", "updated_at", "trashed_at"):
        if hasattr(result.get(key), "isoformat"):
            result[key] = result[key].isoformat()
    return result


class VaultDriveRepository:
    CACHE_TTL_SECONDS = 30.0
    CACHE_MAX_ENTRIES = 256

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def _connect(self):
        return chatty_body_service._connect()

    def invalidate(self, owner_user_id: str, construct_id: str) -> None:
        with self._cache_lock:
            for key in list(self._cache):
                if key[1:3] == (str(owner_user_id), str(construct_id)) or key[1:] == (
                    str(owner_user_id), "__workspace__", "root"
                ):
                    self._cache.pop(key, None)

    @staticmethod
    def _name(value: Any) -> str:
        name = str(value or "").strip()
        if name in {".", ".."} or not _FOLDER_NAME.fullmatch(name):
            raise ValueError("folder name must be 1-255 visible characters without slash")
        return name

    @staticmethod
    def _node_dto(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "nodeId": str(row.get("id")),
            "nodeType": "folder",
            "name": row.get("name"),
            "parentNodeId": str(row.get("parent_node_id")) if row.get("parent_node_id") else None,
            "constructId": row.get("construct_id"),
            "semanticKind": row.get("semantic_kind") or "folder",
            "protected": bool(row.get("protected")),
            "trashed": bool(row.get("trashed_at")),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    @staticmethod
    def _file_dto(row: dict[str, Any]) -> dict[str, Any]:
        path = str(row.get("storage_path") or row.get("filename") or "")
        parts = path.strip("/").split("/")
        semantic_root = parts[2].lower() if len(parts) > 2 else ""
        return {
            "nodeId": str(row.get("id")),
            "fileId": str(row.get("id")),
            "nodeType": "file",
            "name": str(row.get("filename") or "").rstrip("/").rsplit("/", 1)[-1],
            "parentNodeId": str(row.get("drive_parent_node_id")) if row.get("drive_parent_node_id") else None,
            "constructId": row.get("construct_id"),
            "semanticKind": "file",
            "protected": semantic_root in PROTECTED_FILE_ROOTS,
            "trashed": bool(row.get("drive_trashed_at")),
            "sizeBytes": int(row.get("size_bytes") or 0),
            "sha256": row.get("sha256"),
            "contentType": row.get("content_type") or row.get("file_type"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    def _root(self, cur, owner_user_id: str, construct_id: str, *, create: bool) -> dict[str, Any] | None:
        cur.execute(
            """
            SELECT * FROM ovvaults.vault_drive_nodes
            WHERE owner_user_id=%s AND construct_id=%s
              AND parent_node_id IS NULL AND trashed_at IS NULL
            LIMIT 1
            """,
            (owner_user_id, construct_id),
        )
        row = cur.fetchone()
        if row or not create:
            return _dict(row) if row else None
        cur.execute(
            """
            INSERT INTO ovvaults.vault_drive_nodes (
              owner_user_id,construct_id,parent_node_id,name,normalized_name,
              logical_path,semantic_kind,protected,provenance
            ) VALUES (%s,%s,NULL,%s,%s,%s,'instance_root',true,%s::jsonb)
            RETURNING *
            """,
            (
                owner_user_id, construct_id, construct_id, construct_id.lower(),
                f"instances/{construct_id}", json.dumps({"source": "drive_api"}),
            ),
        )
        return _dict(cur.fetchone())

    def _folder(self, cur, owner_user_id: str, construct_id: str, node_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        cur.execute(
            f"""
            SELECT * FROM ovvaults.vault_drive_nodes
            WHERE id=%s AND owner_user_id=%s AND construct_id=%s
            {"FOR UPDATE" if lock else ""}
            """,
            (node_id, owner_user_id, construct_id),
        )
        row = cur.fetchone()
        return _dict(row) if row else None

    def _file(self, cur, owner_user_id: str, construct_id: str, node_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        cur.execute(
            f"""
            SELECT id::text AS id,filename,storage_path,object_key,construct_id,
                   drive_parent_node_id,drive_trashed_at,size_bytes,sha256,
                   content_type,file_type,created_at,updated_at
            FROM ovvaults.vault_files
            WHERE id=%s AND user_id=%s AND construct_id=%s
              AND coalesce(is_system,false)=false
            {"FOR UPDATE" if lock else ""}
            """,
            (node_id, owner_user_id, construct_id),
        )
        row = cur.fetchone()
        return _dict(row) if row else None

    @staticmethod
    def _file_is_protected(file_row: dict[str, Any]) -> bool:
        path = str(file_row.get("storage_path") or file_row.get("filename") or "")
        parts = path.strip("/").split("/")
        return len(parts) > 2 and parts[2].lower() in PROTECTED_FILE_ROOTS

    @staticmethod
    def _upload_context_for_folder(construct_id: str, destination: dict[str, Any]) -> dict[str, Any]:
        root_path = f"instances/{construct_id}"
        relative = str(destination["logical_path"])[len(root_path):].strip("/")
        top = relative.split("/", 1)[0].lower() if relative else ""
        if top in TRANSCRIPT_PROVIDER_ROOTS:
            upload_kind = "transcript"
            knowledge_destination = None
        elif top in {"assets", "documents"}:
            upload_kind = "knowledge"
            knowledge_destination = top
        else:
            raise ValueError("uploads require an assets, documents, or transcript-provider destination")
        return {
            "destinationNodeId": destination["id"],
            "logicalPath": destination["logical_path"],
            "uploadKind": upload_kind,
            "knowledgeDestination": knowledge_destination,
            "provider": top if upload_kind == "transcript" else None,
        }

    def children(self, *, owner_user_id: str, construct_id: str, parent_node_id: str) -> dict[str, Any]:
        try:
            from .relying_party_scope import current_relying_party_id
        except ImportError:  # direct script launcher compatibility
            from relying_party_scope import current_relying_party_id
        key = (current_relying_party_id(), str(owner_user_id), construct_id, parent_node_id or "root")
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] <= self.CACHE_TTL_SECONDS:
                return json.loads(json.dumps({**cached[1], "cacheState": "fresh"}))
        with self._connect() as conn:
            with conn.cursor() as cur:
                parent = self._root(cur, owner_user_id, construct_id, create=False) if parent_node_id == "root" else self._folder(cur, owner_user_id, construct_id, parent_node_id)
                if not parent or parent.get("trashed_at"):
                    raise LookupError("folder not found")
                cur.execute(
                    """
                    SELECT * FROM ovvaults.vault_drive_nodes
                    WHERE owner_user_id=%s AND construct_id=%s AND parent_node_id=%s
                      AND trashed_at IS NULL
                    ORDER BY normalized_name,id
                    """,
                    (owner_user_id, construct_id, parent["id"]),
                )
                folders = [_dict(row) for row in cur.fetchall()]
                cur.execute(
                    """
                    SELECT id::text AS id,filename,storage_path,construct_id,drive_parent_node_id,
                           drive_trashed_at,size_bytes,sha256,content_type,file_type,
                           created_at,updated_at
                    FROM ovvaults.vault_files
                    WHERE user_id=%s AND construct_id=%s AND drive_parent_node_id=%s
                      AND drive_trashed_at IS NULL AND coalesce(is_system,false)=false
                    ORDER BY lower(regexp_replace(filename,'^.*/','')),id
                    """,
                    (owner_user_id, construct_id, parent["id"]),
                )
                files = [_dict(row) for row in cur.fetchall()]
                cur.execute(
                    """
                    WITH RECURSIVE lineage AS (
                      SELECT id,parent_node_id,name,0 AS depth
                      FROM ovvaults.vault_drive_nodes WHERE id=%s
                      UNION ALL
                      SELECT parent.id,parent.parent_node_id,parent.name,lineage.depth+1
                      FROM ovvaults.vault_drive_nodes parent
                      JOIN lineage ON lineage.parent_node_id=parent.id
                    )
                    SELECT id::text AS id,name FROM lineage
                    WHERE parent_node_id IS NOT NULL ORDER BY depth DESC
                    """,
                    (parent["id"],),
                )
                breadcrumbs = [{"nodeId": str(row["id"]), "name": row["name"]} for row in cur.fetchall()]
        payload = {
            "parentNode": self._node_dto(parent),
            "breadcrumbs": breadcrumbs,
            "children": [*(self._node_dto(row) for row in folders), *(self._file_dto(row) for row in files)],
            "count": len(folders) + len(files),
            "cacheState": "miss",
            "refreshing": False,
        }
        with self._cache_lock:
            if len(self._cache) >= self.CACHE_MAX_ENTRIES:
                oldest = min(self._cache, key=lambda item: self._cache[item][0])
                self._cache.pop(oldest, None)
            self._cache[key] = (time.monotonic(), payload)
        return json.loads(json.dumps(payload))

    def workspace_root(
        self, *, owner_user_id: str, constructs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Project the authenticated user's canonical workspace root without writes.

        The user-directory contract is stable even when a namespace currently has
        no materialized files: account/, instances/, and library/ always exist as
        logical roots.  Construct children come only from the already owner-scoped
        projectable construct projection supplied by the caller.  Storage prefixes
        are deliberately not promoted to workspace folders: they are not a Drive
        contract and could expose operational namespaces such as ``system``.
        """
        try:
            from .relying_party_scope import current_relying_party_id
        except ImportError:  # direct script launcher compatibility
            from relying_party_scope import current_relying_party_id
        key = (current_relying_party_id(), str(owner_user_id), "__workspace__", "root")
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] <= self.CACHE_TTL_SECONDS:
                return json.loads(json.dumps({**cached[1], "cacheState": "fresh"}))

        construct_items = sorted(
            (
                {
                    "nodeId": f"instance:{str(item.get('callsign') or item.get('construct_id'))}",
                    "nodeType": "folder",
                    "name": str(item.get("displayName") or item.get("name") or item.get("callsign")),
                    "logicalPath": f"instances/{str(item.get('callsign') or item.get('construct_id'))}",
                    "constructId": str(item.get("callsign") or item.get("construct_id")),
                    "semanticKind": "instance_root",
                    "protected": True,
                    "source": "owner_construct_projection",
                    "sourceRelyingPartyId": str(item.get("sourceRelyingPartyId") or "vvault"),
                    "workspaceRef": str(item.get("workspaceRef") or ""),
                }
                for item in constructs
                if str(item.get("callsign") or item.get("construct_id") or "").strip()
            ),
            key=lambda item: (item["name"].casefold(), item["constructId"]),
        )

        def root_node(name: str, semantic_kind: str, *, protected: bool = True) -> dict[str, Any]:
            return {
                "nodeId": f"workspace:{name}",
                "nodeType": "folder",
                "name": name,
                "logicalPath": name,
                "constructId": None,
                "semanticKind": semantic_kind,
                "protected": protected,
                "source": "vvault_user_directory",
            }

        account = root_node("account", "account_root")
        account["childrenPreview"] = [
            {
                "nodeId": "workspace:account/profile.json",
                "nodeType": "file",
                "name": "profile.json",
                "logicalPath": "account/profile.json",
                "semanticKind": "account_profile",
                "protected": True,
                "source": "vvault_user_directory",
            }
        ]
        instances = root_node("instances", "instances_root")
        instances["childrenPreview"] = construct_items
        library = root_node("library", "library_root", protected=False)
        library["childrenPreview"] = [
            {
                **root_node(name, f"library_{name}", protected=False),
                "nodeId": f"workspace:library/{name}",
                "logicalPath": f"library/{name}",
            }
            for name in ("assets", "gallery", "documents")
        ]
        payload = {
            "projectionSchemaVersion": "1.1.0",
            "root": {
                "nodeId": "workspace:root",
                "nodeType": "folder",
                "name": "My files",
                "logicalPath": "",
                "semanticKind": "workspace_root",
                "protected": True,
            },
            "children": [account, instances, library],
            "count": 3,
            "instanceCount": len(construct_items),
            "cacheState": "miss",
            "refreshing": False,
        }
        with self._cache_lock:
            if len(self._cache) >= self.CACHE_MAX_ENTRIES:
                oldest = min(self._cache, key=lambda item: self._cache[item][0])
                self._cache.pop(oldest, None)
            self._cache[key] = (time.monotonic(), payload)
        return json.loads(json.dumps(payload))

    def _receipt(self, cur, *, owner_user_id: str, construct_id: str, operation: str, node_id: str, detail: dict[str, Any]) -> dict[str, Any]:
        receipt = {
            "schema": "life.vvault.drive-operation/1.0.0",
            "owner_user_id": owner_user_id,
            "construct_id": construct_id,
            "operation": operation,
            "node_id": node_id,
            "timestamp": _now(),
            **detail,
        }
        encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        cur.execute(
            """
            INSERT INTO ovvaults.vault_drive_operation_receipts
              (owner_user_id,construct_id,operation,node_id,receipt,receipt_sha256)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s)
            RETURNING id::text AS id,created_at
            """,
            (owner_user_id, construct_id, operation, node_id, encoded, digest),
        )
        row = cur.fetchone()
        return {"receiptId": str(row["id"]), "receiptSha256": digest, "operation": operation, "createdAt": row["created_at"].isoformat()}

    def create_folder(self, *, owner_user_id: str, construct_id: str, parent_node_id: str, name: str) -> dict[str, Any]:
        name = self._name(name)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                parent = self._root(cur, owner_user_id, construct_id, create=True) if parent_node_id == "root" else self._folder(cur, owner_user_id, construct_id, parent_node_id, lock=True)
                if not parent or parent.get("trashed_at"):
                    raise LookupError("parent folder not found")
                semantic = "transcript_provider" if parent.get("semantic_kind") == "instance_root" and name.lower() in TRANSCRIPT_PROVIDER_ROOTS else "folder"
                protected = parent.get("semantic_kind") == "instance_root" and name.lower() in PROTECTED_ROOTS
                if protected:
                    semantic = name.lower()
                path = f"{parent['logical_path']}/{name}"
                cur.execute(
                    """
                    INSERT INTO ovvaults.vault_drive_nodes (
                      owner_user_id,construct_id,parent_node_id,name,normalized_name,
                      logical_path,semantic_kind,protected,provenance
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    RETURNING *
                    """,
                    (owner_user_id, construct_id, parent["id"], name, name.casefold(), path, semantic, protected, json.dumps({"source": "drive_api"})),
                )
                folder = _dict(cur.fetchone())
                receipt = self._receipt(cur, owner_user_id=owner_user_id, construct_id=construct_id, operation="create_folder", node_id=folder["id"], detail={"after": {"parent_node_id": parent["id"], "path": path}})
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"folder": self._node_dto(folder), "operationReceipt": receipt}

    def ensure_construct_root(self, *, owner_user_id: str, construct_id: str) -> dict[str, Any]:
        """Materialize the canonical empty instance root during construct creation."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                root = self._root(cur, owner_user_id, construct_id, create=True)
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return self._node_dto(root)

    def remove_empty_construct_root(self, *, owner_user_id: str, construct_id: str) -> None:
        """Compensate a failed create only when the new Drive root has no children/files."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM ovvaults.vault_drive_nodes root
                    WHERE root.owner_user_id=%s AND root.construct_id=%s
                      AND root.parent_node_id IS NULL
                      AND root.provenance->>'source'='drive_api'
                      AND NOT EXISTS (SELECT 1 FROM ovvaults.vault_drive_nodes child WHERE child.parent_node_id=root.id)
                      AND NOT EXISTS (SELECT 1 FROM ovvaults.vault_files file WHERE file.drive_parent_node_id=root.id)
                    """,
                    (owner_user_id, construct_id),
                )
            conn.commit()
        self.invalidate(owner_user_id, construct_id)

    def mutate_folder(self, *, owner_user_id: str, construct_id: str, node_id: str, name: str | None = None, parent_node_id: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                folder = self._folder(cur, owner_user_id, construct_id, node_id, lock=True)
                if not folder or folder.get("trashed_at"):
                    raise LookupError("folder not found")
                if folder.get("protected"):
                    raise PermissionError("protected semantic folders cannot be renamed or moved")
                target_parent = folder
                if parent_node_id is not None:
                    target_parent = self._root(cur, owner_user_id, construct_id, create=False) if parent_node_id == "root" else self._folder(cur, owner_user_id, construct_id, parent_node_id, lock=True)
                    if not target_parent or target_parent.get("trashed_at"):
                        raise LookupError("destination folder not found")
                    if target_parent["logical_path"].startswith(f"{folder['logical_path']}/") or target_parent["id"] == folder["id"]:
                        raise ValueError("folder cannot move inside itself")
                else:
                    target_parent = self._folder(cur, owner_user_id, construct_id, folder["parent_node_id"], lock=True)
                new_name = self._name(name) if name is not None else folder["name"]
                new_path = f"{target_parent['logical_path']}/{new_name}"
                old_path = folder["logical_path"]
                operation = "move" if str(target_parent["id"]) != str(folder["parent_node_id"]) else "rename"
                cur.execute(
                    """
                    UPDATE ovvaults.vault_drive_nodes
                    SET parent_node_id=CASE WHEN id=%s THEN %s ELSE parent_node_id END,
                        name=CASE WHEN id=%s THEN %s ELSE name END,
                        normalized_name=CASE WHEN id=%s THEN %s ELSE normalized_name END,
                        logical_path=%s || substring(logical_path from char_length(%s)+1),updated_at=now()
                    WHERE owner_user_id=%s AND construct_id=%s
                      AND (id=%s OR logical_path LIKE %s)
                    """,
                    (
                        folder["id"], target_parent["id"],
                        folder["id"], new_name,
                        folder["id"], new_name.casefold(),
                        new_path, old_path, owner_user_id, construct_id,
                        folder["id"], f"{old_path}/%",
                    ),
                )
                cur.execute(
                    """
                    UPDATE ovvaults.vault_files
                    SET filename=%s || substring(filename from char_length(%s)+1),
                        storage_path=%s || substring(storage_path from char_length(%s)+1),
                        object_key=replace(object_key,%s,%s),updated_at=updated_at
                    WHERE user_id=%s AND construct_id=%s
                      AND (filename LIKE %s OR storage_path LIKE %s)
                    """,
                    (new_path, old_path, new_path, old_path, old_path, new_path, owner_user_id, construct_id, f"{old_path}/%", f"{old_path}/%"),
                )
                affected_files = cur.rowcount
                receipt = self._receipt(cur, owner_user_id=owner_user_id, construct_id=construct_id, operation=operation, node_id=folder["id"], detail={"before": {"parent_node_id": folder["parent_node_id"], "path": old_path}, "after": {"parent_node_id": target_parent["id"], "path": new_path}, "affected_files": affected_files})
                updated = self._folder(cur, owner_user_id, construct_id, folder["id"])
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"node": self._node_dto(updated), "operationReceipt": receipt}

    def mutate_node(self, *, owner_user_id: str, construct_id: str, node_id: str, name: str | None = None, parent_node_id: str | None = None) -> dict[str, Any]:
        """Rename/move a folder or file through the shared Drive node route."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                folder = self._folder(cur, owner_user_id, construct_id, node_id)
        if folder:
            return self.mutate_folder(
                owner_user_id=owner_user_id,
                construct_id=construct_id,
                node_id=node_id,
                name=name,
                parent_node_id=parent_node_id,
            )

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                file_row = self._file(cur, owner_user_id, construct_id, node_id, lock=True)
                if not file_row or file_row.get("drive_trashed_at"):
                    raise LookupError("node not found")
                if self._file_is_protected(file_row):
                    raise PermissionError("protected semantic core files cannot be renamed or moved")
                old_path = str(file_row.get("storage_path") or file_row.get("filename") or "")
                old_name = old_path.rstrip("/").rsplit("/", 1)[-1]
                new_name = self._name(name) if name is not None else old_name
                if parent_node_id is None:
                    destination = self._folder(
                        cur, owner_user_id, construct_id,
                        str(file_row.get("drive_parent_node_id")), lock=True,
                    )
                else:
                    destination = self._root(cur, owner_user_id, construct_id, create=False) if parent_node_id == "root" else self._folder(cur, owner_user_id, construct_id, parent_node_id, lock=True)
                if not destination or destination.get("trashed_at"):
                    raise LookupError("destination folder not found")
                # Files may only live in a canonical upload-bearing semantic branch.
                context = self._upload_context_for_folder(construct_id, destination)
                new_path = f"{context['logicalPath']}/{new_name}"
                operation = "move" if str(destination["id"]) != str(file_row.get("drive_parent_node_id")) else "rename"
                cur.execute(
                    """
                    UPDATE ovvaults.vault_files
                    SET filename=%s,storage_path=%s,
                        object_key=CASE WHEN object_key IS NULL THEN NULL ELSE replace(object_key,%s,%s) END,
                        drive_parent_node_id=%s,updated_at=updated_at
                    WHERE id=%s AND user_id=%s AND construct_id=%s
                    RETURNING id::text AS id,filename,storage_path,construct_id,
                              drive_parent_node_id,drive_trashed_at,size_bytes,sha256,
                              content_type,file_type,created_at,updated_at
                    """,
                    (new_path, new_path, old_path, new_path, destination["id"], node_id, owner_user_id, construct_id),
                )
                updated = _dict(cur.fetchone())
                receipt = self._receipt(
                    cur,
                    owner_user_id=owner_user_id,
                    construct_id=construct_id,
                    operation=operation,
                    node_id=node_id,
                    detail={
                        "before": {"parent_node_id": file_row.get("drive_parent_node_id"), "path": old_path},
                        "after": {"parent_node_id": destination["id"], "path": new_path},
                        "sha256": file_row.get("sha256"),
                    },
                )
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"node": self._file_dto(updated), "operationReceipt": receipt}

    def set_trashed(self, *, owner_user_id: str, construct_id: str, node_id: str, restore: bool) -> dict[str, Any]:
        operation = "restore" if restore else "trash"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                folder = self._folder(cur, owner_user_id, construct_id, node_id, lock=True)
                if not folder:
                    raise LookupError("folder not found")
                if folder.get("protected"):
                    raise PermissionError("protected semantic folders cannot be trashed")
                old_path = folder["logical_path"]
                cur.execute(
                    """
                    UPDATE ovvaults.vault_drive_nodes SET trashed_at=%s,updated_at=now()
                    WHERE owner_user_id=%s AND construct_id=%s
                      AND (id=%s OR logical_path LIKE %s)
                    """,
                    (None if restore else _now(), owner_user_id, construct_id, node_id, f"{old_path}/%"),
                )
                cur.execute(
                    """
                    UPDATE ovvaults.vault_files SET drive_trashed_at=%s
                    WHERE user_id=%s AND construct_id=%s
                      AND (filename LIKE %s OR storage_path LIKE %s)
                    """,
                    (None if restore else _now(), owner_user_id, construct_id, f"{old_path}/%", f"{old_path}/%"),
                )
                receipt = self._receipt(cur, owner_user_id=owner_user_id, construct_id=construct_id, operation=operation, node_id=node_id, detail={"path": old_path, "trashed": not restore})
                updated = self._folder(cur, owner_user_id, construct_id, node_id)
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"node": self._node_dto(updated), "operationReceipt": receipt}

    def set_node_trashed(self, *, owner_user_id: str, construct_id: str, node_id: str, restore: bool) -> dict[str, Any]:
        """Soft-trash/restore a folder subtree or one file node."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                folder = self._folder(cur, owner_user_id, construct_id, node_id)
        if folder:
            return self.set_trashed(
                owner_user_id=owner_user_id,
                construct_id=construct_id,
                node_id=node_id,
                restore=restore,
            )
        operation = "restore" if restore else "trash"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                file_row = self._file(cur, owner_user_id, construct_id, node_id, lock=True)
                if not file_row:
                    raise LookupError("node not found")
                if self._file_is_protected(file_row):
                    raise PermissionError("protected semantic core files cannot be trashed")
                trashed_at = None if restore else _now()
                cur.execute(
                    """
                    UPDATE ovvaults.vault_files SET drive_trashed_at=%s,updated_at=updated_at
                    WHERE id=%s AND user_id=%s AND construct_id=%s
                    RETURNING id::text AS id,filename,storage_path,construct_id,
                              drive_parent_node_id,drive_trashed_at,size_bytes,sha256,
                              content_type,file_type,created_at,updated_at
                    """,
                    (trashed_at, node_id, owner_user_id, construct_id),
                )
                updated = _dict(cur.fetchone())
                receipt = self._receipt(
                    cur,
                    owner_user_id=owner_user_id,
                    construct_id=construct_id,
                    operation=operation,
                    node_id=node_id,
                    detail={
                        "path": file_row.get("storage_path") or file_row.get("filename"),
                        "sha256": file_row.get("sha256"),
                        "trashed": not restore,
                    },
                )
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"node": self._file_dto(updated), "operationReceipt": receipt}

    def trash(self, *, owner_user_id: str, construct_id: str | None = None) -> dict[str, Any]:
        """List top-level soft-deleted nodes; descendants stay represented by their subtree."""
        params: list[Any] = [owner_user_id]
        construct_sql = ""
        if construct_id:
            construct_sql = " AND node.construct_id=%s"
            params.append(construct_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT node.* FROM ovvaults.vault_drive_nodes node
                    LEFT JOIN ovvaults.vault_drive_nodes parent ON parent.id=node.parent_node_id
                    WHERE node.owner_user_id=%s AND node.trashed_at IS NOT NULL
                      {construct_sql}
                      AND (parent.id IS NULL OR parent.trashed_at IS NULL)
                    ORDER BY node.trashed_at DESC,node.normalized_name,node.id
                    """,
                    tuple(params),
                )
                folders = [_dict(row) for row in cur.fetchall()]
                file_params: list[Any] = [owner_user_id]
                file_construct_sql = ""
                if construct_id:
                    file_construct_sql = " AND file.construct_id=%s"
                    file_params.append(construct_id)
                cur.execute(
                    f"""
                    SELECT file.id::text AS id,file.filename,file.storage_path,file.construct_id,
                           file.drive_parent_node_id,file.drive_trashed_at,file.size_bytes,file.sha256,
                           file.content_type,file.file_type,file.created_at,file.updated_at
                    FROM ovvaults.vault_files file
                    LEFT JOIN ovvaults.vault_drive_nodes parent ON parent.id=file.drive_parent_node_id
                    WHERE file.user_id=%s AND file.drive_trashed_at IS NOT NULL
                      {file_construct_sql}
                      AND coalesce(file.is_system,false)=false
                      AND (parent.id IS NULL OR parent.trashed_at IS NULL)
                    ORDER BY file.drive_trashed_at DESC,lower(file.filename),file.id
                    """,
                    tuple(file_params),
                )
                files = [_dict(row) for row in cur.fetchall()]
        items = []
        for row in folders:
            dto = self._node_dto(row)
            dto.update({"originalPath": row.get("logical_path"), "deletedAt": row.get("trashed_at"), "sizeBytes": None})
            items.append(dto)
        for row in files:
            dto = self._file_dto(row)
            dto.update({"originalPath": row.get("storage_path") or row.get("filename"), "deletedAt": row.get("drive_trashed_at")})
            items.append(dto)
        items.sort(key=lambda item: (str(item.get("deletedAt") or ""), item.get("name") or ""), reverse=True)
        return {"items": items, "count": len(items), "cacheState": "miss", "refreshing": False}

    def trash_nodes_atomic(self, *, owner_user_id: str, construct_id: str, node_ids: list[str]) -> dict[str, Any]:
        """Soft-delete a selected set in one transaction after validating every node."""
        ordered = list(dict.fromkeys(str(value) for value in node_ids if value))
        if not ordered or len(ordered) > 200:
            raise ValueError("nodeIds must contain 1-200 items")
        timestamp = _now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                resolved = []
                for node_id in ordered:
                    folder = self._folder(cur, owner_user_id, construct_id, node_id, lock=True)
                    if folder:
                        if folder.get("protected") or folder.get("semantic_kind") == "instance_root":
                            raise PermissionError("protected semantic folders cannot be trashed")
                        if folder.get("trashed_at"):
                            raise ValueError("node is already in Trash")
                        resolved.append(("folder", folder))
                        continue
                    file_row = self._file(cur, owner_user_id, construct_id, node_id, lock=True)
                    if not file_row:
                        raise LookupError("node not found")
                    if self._file_is_protected(file_row):
                        raise PermissionError("protected semantic core files cannot be trashed")
                    if file_row.get("drive_trashed_at"):
                        raise ValueError("node is already in Trash")
                    resolved.append(("file", file_row))
                # Descendants selected with a selected ancestor are represented by that subtree.
                folder_paths = [row["logical_path"] for kind, row in resolved if kind == "folder"]
                applied = []
                for kind, row in resolved:
                    path = str(row.get("logical_path") or row.get("storage_path") or row.get("filename") or "")
                    if any(path.startswith(parent + "/") for parent in folder_paths if parent != path):
                        continue
                    if kind == "folder":
                        cur.execute("UPDATE ovvaults.vault_drive_nodes SET trashed_at=%s,updated_at=now() WHERE owner_user_id=%s AND construct_id=%s AND (id=%s OR logical_path LIKE %s)", (timestamp, owner_user_id, construct_id, row["id"], f"{path}/%"))
                        cur.execute("UPDATE ovvaults.vault_files SET drive_trashed_at=%s WHERE user_id=%s AND construct_id=%s AND (filename LIKE %s OR storage_path LIKE %s)", (timestamp, owner_user_id, construct_id, f"{path}/%", f"{path}/%"))
                    else:
                        cur.execute("UPDATE ovvaults.vault_files SET drive_trashed_at=%s,updated_at=updated_at WHERE id=%s AND user_id=%s AND construct_id=%s", (timestamp, row["id"], owner_user_id, construct_id))
                    applied.append({"nodeId": row["id"], "nodeType": kind, "path": path, "sha256": row.get("sha256")})
                receipt = self._receipt(cur, owner_user_id=owner_user_id, construct_id=construct_id, operation="batch_trash" if len(applied) > 1 else "trash", node_id=applied[0]["nodeId"], detail={"items": applied, "trashed_at": timestamp})
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"trashed": applied, "count": len(applied), "operationReceipt": receipt}

    def move_nodes_atomic(self, *, owner_user_id: str, construct_id: str, node_ids: list[str], parent_node_id: str) -> dict[str, Any]:
        """Move selected sibling nodes atomically after validating the full set."""
        ordered = list(dict.fromkeys(str(value) for value in node_ids if value))
        if not ordered or len(ordered) > 200:
            raise ValueError("nodeIds must contain 1-200 items")
        moved = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                destination = self._root(cur, owner_user_id, construct_id, create=False) if parent_node_id == "root" else self._folder(cur, owner_user_id, construct_id, parent_node_id, lock=True)
                if not destination or destination.get("trashed_at"):
                    raise LookupError("destination folder not found")
                resolved = []
                for node_id in ordered:
                    folder = self._folder(cur, owner_user_id, construct_id, node_id, lock=True)
                    if folder:
                        if folder.get("protected") or folder.get("trashed_at"):
                            raise PermissionError("protected or trashed folders cannot be moved")
                        if destination["logical_path"].startswith(folder["logical_path"] + "/") or destination["id"] == folder["id"]:
                            raise ValueError("folder cannot move inside itself")
                        resolved.append(("folder", folder)); continue
                    file_row = self._file(cur, owner_user_id, construct_id, node_id, lock=True)
                    if not file_row or file_row.get("drive_trashed_at"):
                        raise LookupError("node not found")
                    if self._file_is_protected(file_row):
                        raise PermissionError("protected semantic core files cannot be moved")
                    resolved.append(("file", file_row))
                context = self._upload_context_for_folder(construct_id, destination)
                target_names: set[str] = set()
                for kind, row in resolved:
                    old_path = str(row.get("logical_path") or row.get("storage_path") or row.get("filename"))
                    name = old_path.rstrip("/").rsplit("/",1)[-1]
                    normalized = name.casefold()
                    if normalized in target_names:
                        raise ValueError("selected items contain a destination name collision")
                    target_names.add(normalized)
                    new_path = f"{destination['logical_path']}/{name}"
                    if kind == "folder":
                        cur.execute("SELECT 1 FROM ovvaults.vault_drive_nodes WHERE owner_user_id=%s AND construct_id=%s AND parent_node_id=%s AND normalized_name=%s AND trashed_at IS NULL AND id<>%s", (owner_user_id,construct_id,destination["id"],normalized,row["id"]))
                        if cur.fetchone(): raise ValueError(f"destination already contains {name}")
                        cur.execute("UPDATE ovvaults.vault_drive_nodes SET parent_node_id=CASE WHEN id=%s THEN %s ELSE parent_node_id END,logical_path=%s || substring(logical_path from char_length(%s)+1),updated_at=now() WHERE owner_user_id=%s AND construct_id=%s AND (id=%s OR logical_path LIKE %s)", (row["id"],destination["id"],new_path,old_path,owner_user_id,construct_id,row["id"],f"{old_path}/%"))
                        cur.execute("UPDATE ovvaults.vault_files SET filename=%s || substring(filename from char_length(%s)+1),storage_path=%s || substring(storage_path from char_length(%s)+1),object_key=CASE WHEN object_key IS NULL THEN NULL ELSE replace(object_key,%s,%s) END,updated_at=updated_at WHERE user_id=%s AND construct_id=%s AND (filename LIKE %s OR storage_path LIKE %s)", (new_path,old_path,new_path,old_path,old_path,new_path,owner_user_id,construct_id,f"{old_path}/%",f"{old_path}/%"))
                    else:
                        cur.execute("SELECT 1 FROM ovvaults.vault_files WHERE user_id=%s AND construct_id=%s AND drive_trashed_at IS NULL AND (filename=%s OR storage_path=%s) AND id<>%s", (owner_user_id,construct_id,new_path,new_path,row["id"]))
                        if cur.fetchone(): raise ValueError(f"destination already contains {name}")
                        cur.execute("UPDATE ovvaults.vault_files SET filename=%s,storage_path=%s,object_key=CASE WHEN object_key IS NULL THEN NULL ELSE replace(object_key,%s,%s) END,drive_parent_node_id=%s,updated_at=updated_at WHERE id=%s AND user_id=%s AND construct_id=%s", (new_path,new_path,old_path,new_path,destination["id"],row["id"],owner_user_id,construct_id))
                    moved.append({"nodeId":row["id"],"nodeType":kind,"beforePath":old_path,"afterPath":new_path,"sha256":row.get("sha256")})
                receipt = self._receipt(cur, owner_user_id=owner_user_id, construct_id=construct_id, operation="batch_move", node_id=moved[0]["nodeId"], detail={"destination_node_id":destination["id"],"items":moved})
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"moved":moved,"count":len(moved),"operationReceipt":receipt,"destinationClassification":context}

    def restore_nodes_atomic(self, *, owner_user_id: str, construct_id: str, node_ids: list[str]) -> dict[str, Any]:
        """Restore selected Trash roots, deterministically suffixing occupied names."""
        ordered = list(dict.fromkeys(str(value) for value in node_ids if value))
        if not ordered or len(ordered) > 200:
            raise ValueError("nodeIds must contain 1-200 items")
        restored = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                for node_id in ordered:
                    folder = self._folder(cur, owner_user_id, construct_id, node_id, lock=True)
                    if folder:
                        if not folder.get("trashed_at"):
                            raise ValueError("node is not in Trash")
                        parent = self._folder(cur, owner_user_id, construct_id, folder["parent_node_id"], lock=True)
                        if not parent or parent.get("trashed_at"):
                            raise ValueError("restore the parent folder first")
                        base_name = folder["name"]
                        name = base_name
                        suffix = 2
                        while True:
                            cur.execute("SELECT 1 FROM ovvaults.vault_drive_nodes WHERE owner_user_id=%s AND construct_id=%s AND parent_node_id=%s AND normalized_name=%s AND trashed_at IS NULL", (owner_user_id, construct_id, parent["id"], name.casefold()))
                            if not cur.fetchone(): break
                            name = f"{base_name} (restored {suffix})"; suffix += 1
                        old_path = folder["logical_path"]
                        new_path = f"{parent['logical_path']}/{name}"
                        cur.execute("UPDATE ovvaults.vault_drive_nodes SET name=CASE WHEN id=%s THEN %s ELSE name END,normalized_name=CASE WHEN id=%s THEN %s ELSE normalized_name END,logical_path=%s || substring(logical_path from char_length(%s)+1),trashed_at=NULL,updated_at=now() WHERE owner_user_id=%s AND construct_id=%s AND (id=%s OR logical_path LIKE %s)", (node_id,name,node_id,name.casefold(),new_path,old_path,owner_user_id,construct_id,node_id,f"{old_path}/%"))
                        cur.execute("UPDATE ovvaults.vault_files SET filename=%s || substring(filename from char_length(%s)+1),storage_path=%s || substring(storage_path from char_length(%s)+1),object_key=CASE WHEN object_key IS NULL THEN NULL ELSE replace(object_key,%s,%s) END,drive_trashed_at=NULL,updated_at=updated_at WHERE user_id=%s AND construct_id=%s AND (filename LIKE %s OR storage_path LIKE %s)", (new_path,old_path,new_path,old_path,old_path,new_path,owner_user_id,construct_id,f"{old_path}/%",f"{old_path}/%"))
                        restored.append({"nodeId": node_id, "nodeType": "folder", "path": new_path, "collisionRenamed": name != base_name})
                        continue
                    file_row = self._file(cur, owner_user_id, construct_id, node_id, lock=True)
                    if not file_row or not file_row.get("drive_trashed_at"):
                        raise LookupError("trashed node not found")
                    parent = self._folder(cur, owner_user_id, construct_id, file_row["drive_parent_node_id"], lock=True)
                    if not parent or parent.get("trashed_at"):
                        raise ValueError("restore the parent folder first")
                    old_path = str(file_row.get("storage_path") or file_row.get("filename"))
                    base_name = old_path.rsplit("/",1)[-1]; name = base_name; suffix = 2
                    while True:
                        candidate = f"{parent['logical_path']}/{name}"
                        cur.execute("SELECT 1 FROM ovvaults.vault_files WHERE user_id=%s AND construct_id=%s AND drive_trashed_at IS NULL AND (filename=%s OR storage_path=%s)", (owner_user_id,construct_id,candidate,candidate))
                        if not cur.fetchone(): break
                        stem, dot, ext = base_name.rpartition(".")
                        name = f"{stem or base_name} (restored {suffix}){dot + ext if dot else ''}"; suffix += 1
                    cur.execute("UPDATE ovvaults.vault_files SET filename=%s,storage_path=%s,object_key=CASE WHEN object_key IS NULL THEN NULL ELSE replace(object_key,%s,%s) END,drive_trashed_at=NULL,updated_at=updated_at WHERE id=%s AND user_id=%s AND construct_id=%s", (candidate,candidate,old_path,candidate,node_id,owner_user_id,construct_id))
                    restored.append({"nodeId": node_id, "nodeType": "file", "path": candidate, "collisionRenamed": name != base_name})
                receipt = self._receipt(cur, owner_user_id=owner_user_id, construct_id=construct_id, operation="restore_all" if len(restored)>1 else "restore", node_id=restored[0]["nodeId"], detail={"items": restored})
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"restored": restored, "count": len(restored), "operationReceipt": receipt}

    def permanently_delete_nodes(self, *, owner_user_id: str, construct_id: str, node_ids: list[str], empty_trash: bool = False) -> dict[str, Any]:
        """Permanently remove only already-trashed owner rows, preserving an immutable receipt."""
        ordered = list(dict.fromkeys(str(value) for value in node_ids if value))
        if not ordered:
            raise ValueError("nodeIds are required")
        deleted = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                for node_id in ordered:
                    folder = self._folder(cur, owner_user_id, construct_id, node_id, lock=True)
                    if folder:
                        if not folder.get("trashed_at") or folder.get("protected"):
                            raise PermissionError("only eligible Trash items can be permanently deleted")
                        path = folder["logical_path"]
                        cur.execute("SELECT id::text,sha256,storage_path FROM ovvaults.vault_files WHERE user_id=%s AND construct_id=%s AND drive_trashed_at IS NOT NULL AND (filename LIKE %s OR storage_path LIKE %s) ORDER BY id", (owner_user_id,construct_id,f"{path}/%",f"{path}/%"))
                        descendants = [dict(row) for row in cur.fetchall()]
                        deleted.append({"nodeId":node_id,"nodeType":"folder","path":path,"descendantFiles":descendants})
                    else:
                        file_row = self._file(cur, owner_user_id, construct_id, node_id, lock=True)
                        if not file_row or not file_row.get("drive_trashed_at") or self._file_is_protected(file_row):
                            raise PermissionError("only eligible Trash items can be permanently deleted")
                        deleted.append({"nodeId":node_id,"nodeType":"file","path":file_row.get("storage_path"),"sha256":file_row.get("sha256")})
                receipt = self._receipt(cur, owner_user_id=owner_user_id, construct_id=construct_id, operation="empty_trash" if empty_trash else "permanent_delete", node_id=ordered[0], detail={"items":deleted,"confirmed":True})
                for item in deleted:
                    if item["nodeType"] == "folder":
                        path=item["path"]
                        cur.execute("DELETE FROM ovvaults.vault_files WHERE user_id=%s AND construct_id=%s AND drive_trashed_at IS NOT NULL AND (filename LIKE %s OR storage_path LIKE %s)", (owner_user_id,construct_id,f"{path}/%",f"{path}/%"))
                        cur.execute("DELETE FROM ovvaults.vault_drive_nodes WHERE owner_user_id=%s AND construct_id=%s AND trashed_at IS NOT NULL AND (id=%s OR logical_path LIKE %s)", (owner_user_id,construct_id,item["nodeId"],f"{path}/%"))
                    else:
                        cur.execute("DELETE FROM ovvaults.vault_files WHERE id=%s AND user_id=%s AND construct_id=%s AND drive_trashed_at IS NOT NULL", (item["nodeId"],owner_user_id,construct_id))
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return {"deleted": [{k:v for k,v in item.items() if k != "descendantFiles"} for item in deleted], "count":len(deleted), "operationReceipt":receipt}

    def ensure_upload_path(self, *, owner_user_id: str, construct_id: str, destination_node_id: str, relative_directory: str) -> dict[str, Any]:
        parts = [self._name(part) for part in str(relative_directory or "").split("/") if part]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"drive:{owner_user_id}:{construct_id}",))
                current = self._root(cur, owner_user_id, construct_id, create=True) if destination_node_id == "root" else self._folder(cur, owner_user_id, construct_id, destination_node_id, lock=True)
                if not current:
                    raise LookupError("destination folder not found")
                for name in parts:
                    cur.execute(
                        """SELECT * FROM ovvaults.vault_drive_nodes
                           WHERE owner_user_id=%s AND construct_id=%s AND parent_node_id=%s
                             AND normalized_name=%s AND trashed_at IS NULL""",
                        (owner_user_id, construct_id, current["id"], name.casefold()),
                    )
                    row = cur.fetchone()
                    if row:
                        current = _dict(row)
                        continue
                    is_instance_child = current.get("semantic_kind") == "instance_root"
                    protected = is_instance_child and name.lower() in PROTECTED_ROOTS
                    semantic = (
                        "transcript_provider"
                        if is_instance_child and name.lower() in TRANSCRIPT_PROVIDER_ROOTS
                        else name.lower()
                        if protected
                        else "folder"
                    )
                    cur.execute(
                        """INSERT INTO ovvaults.vault_drive_nodes
                           (owner_user_id,construct_id,parent_node_id,name,normalized_name,logical_path,semantic_kind,protected,provenance)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING *""",
                        (owner_user_id, construct_id, current["id"], name, name.casefold(), f"{current['logical_path']}/{name}", semantic, protected, json.dumps({"source": "folder_upload"})),
                    )
                    current = _dict(cur.fetchone())
            conn.commit()
        self.invalidate(owner_user_id, construct_id)
        return current

    def upload_context(self, *, owner_user_id: str, construct_id: str, destination_node_id: str) -> dict[str, Any]:
        """Resolve an upload destination from canonical ancestry, not UI labels."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                destination = self._root(cur, owner_user_id, construct_id, create=False) if destination_node_id == "root" else self._folder(cur, owner_user_id, construct_id, destination_node_id)
                if not destination or destination.get("trashed_at"):
                    raise LookupError("destination folder not found")
                return self._upload_context_for_folder(construct_id, destination)

    def bind_file(self, *, owner_user_id: str, construct_id: str, file_id: str, parent_node_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE ovvaults.vault_files SET drive_parent_node_id=%s
                       WHERE id=%s AND user_id=%s AND construct_id=%s""",
                    (parent_node_id, file_id, owner_user_id, construct_id),
                )
                if cur.rowcount != 1:
                    raise LookupError("uploaded file could not be bound to destination folder")
            conn.commit()
        self.invalidate(owner_user_id, construct_id)

    def record_folder_upload(
        self,
        *,
        owner_user_id: str,
        construct_id: str,
        destination_node_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append evidence for a completed folder-aware upload batch."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                destination = self._folder(
                    cur, owner_user_id, construct_id, destination_node_id
                )
                if not destination or destination.get("trashed_at"):
                    raise LookupError("destination folder not found")
                receipt = self._receipt(
                    cur,
                    owner_user_id=owner_user_id,
                    construct_id=construct_id,
                    operation="folder_upload",
                    node_id=destination_node_id,
                    detail={
                        "destination_path": destination["logical_path"],
                        "items": [
                            {
                                "file_id": str(item.get("fileId")),
                                "parent_node_id": str(item.get("parentNodeId")),
                                "path": item.get("path"),
                                "sha256": item.get("sha256"),
                                "status": item.get("status"),
                            }
                            for item in items
                        ],
                    },
                )
            conn.commit()
        return receipt


VAULT_DRIVE_REPOSITORY = VaultDriveRepository()
