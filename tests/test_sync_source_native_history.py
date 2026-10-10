import argparse
import json
from pathlib import Path

import pytest

from scripts.operations import sync_source_native_history as subject


OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
THREAD = "43e73643-dfd6-4958-90b1-8fd7e97c93c6"


def _codex(path: Path, *, thread_source="user", zenith_binding=False):
    rows = [
        {"type": "session_meta", "payload": {"id": THREAD, "timestamp": "2026-01-01T00:00:00Z", "thread_source": thread_source, "source": "vscode"}},
        {"timestamp": "2026-01-01T00:00:00Z", "type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": ("You are Zenith Vale Woodson the Systems Steward.\nUse `vvault/server/life_capsule_resolver.py` as the identity authority for Zenith of Codex." if zenith_binding else "ordinary contract")}] }},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "private"}]}},
        {"timestamp": "2026-01-01T00:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "reply"}]}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _args(root, *, codex=(), exports=(), apply=False, policy="stop"):
    return argparse.Namespace(root=[root], codex_file=list(codex), export_file=list(exports),
                              apply=apply, owner_user_id=OWNER if apply else None,
                              relying_party_id="vvault", failure_policy=policy)


class FakeService:
    def __init__(self, calls, fail_at=None): self.calls, self.fail_at = calls, fail_at
    def ingest_and_project_vault_file(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_at == len(self.calls): raise RuntimeError("fail closed")
        return {"result": "applied"}


def test_default_dry_run_is_count_only_and_never_constructs_service(tmp_path):
    codex = tmp_path / "rollout.jsonl"
    export = tmp_path / "note.md"
    _codex(codex)
    export.write_text("history", encoding="utf-8")

    result = subject.run(_args(tmp_path, codex=[codex], exports=[("cursor", export)]),
                         service_factory=lambda: (_ for _ in ()).throw(AssertionError("DB access")))

    assert result == {
        "mode": "dry-run", "failurePolicy": "stop", "inputFiles": 2,
        "candidateRecords": 2, "acceptedRecords": 2, "appliedRecords": 0,
        "alreadyAppliedRecords": 0, "rejectedRecords": 0, "failedRecords": 0,
        "codexAccountPrivateRecords": 0, "legacyUnclassifiedRecords": 2,
        "stopped": False,
    }
    assert "private" not in json.dumps(result)


def test_apply_installs_scope_and_projects_codex_and_generic_conservatively(tmp_path, monkeypatch):
    codex = tmp_path / "rollout.jsonl"
    export = tmp_path / "note.txt"
    _codex(codex)
    export.write_bytes(b"\xef\xbb\xbflegacy\r\ntext")
    calls, scope = [], []
    monkeypatch.setattr(subject, "set_relying_party_id", lambda value: scope.append(("rp", value)))
    monkeypatch.setattr(subject, "set_authenticated_user_id", lambda value: scope.append(("owner", value)))

    result = subject.run(_args(tmp_path, codex=[codex], exports=[("cursor", export)], apply=True),
                         service_factory=lambda: FakeService(calls))

    assert scope == [("rp", "vvault"), ("owner", OWNER)]
    assert result["appliedRecords"] == 2
    codex_call, generic_call = calls
    assert codex_call["explicit_construct_evidence"] is False
    assert codex_call["construct_id"] is None
    assert codex_call["stable_source_id"] == THREAD
    assert codex_call["source_locator"] == '["root-01/rollout.jsonl"]'
    assert generic_call["explicit_construct_evidence"] is False
    assert generic_call["construct_id"] is None
    assert generic_call["source_locator"] == "root-01/note.txt"
    assert str(tmp_path) not in json.dumps(generic_call["source_metadata"])


def test_apply_promotes_only_authoritative_zenith_binding(tmp_path, monkeypatch):
    codex = tmp_path / "rollout.jsonl"
    _codex(codex, zenith_binding=True)
    calls = []
    monkeypatch.setattr(subject, "set_relying_party_id", lambda _value: None)
    monkeypatch.setattr(subject, "set_authenticated_user_id", lambda _value: None)
    result = subject.run(_args(tmp_path, codex=[codex], apply=True),
                         service_factory=lambda: FakeService(calls))
    assert result["codexAccountPrivateRecords"] == 1
    assert result["legacyUnclassifiedRecords"] == 0
    assert calls[0]["explicit_construct_evidence"] is True
    assert calls[0]["construct_id"] == "zen-001"


def test_outside_root_fails_closed_without_service(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("no", encoding="utf-8")
    result = subject.run(_args(root, exports=[("legacy", outside)]),
                         service_factory=lambda: (_ for _ in ()).throw(AssertionError("DB access")))
    assert result["rejectedRecords"] == 1
    assert result["stopped"] is True
    assert result["candidateRecords"] == 0


def test_rejected_codex_human_gate_continues_only_when_requested(tmp_path):
    rejected = tmp_path / "agent.jsonl"
    accepted = tmp_path / "note.md"
    _codex(rejected, thread_source="subagent")
    accepted.write_text("history", encoding="utf-8")
    result = subject.run(_args(tmp_path, codex=[rejected], exports=[("legacy", accepted)], policy="continue"))
    assert result["rejectedRecords"] == 1
    assert result["legacyUnclassifiedRecords"] == 1
    assert result["stopped"] is False


def test_apply_stop_policy_does_not_attempt_later_records(tmp_path, monkeypatch):
    one = tmp_path / "one.md"
    two = tmp_path / "two.md"
    one.write_text("one", encoding="utf-8")
    two.write_text("two", encoding="utf-8")
    calls = []
    monkeypatch.setattr(subject, "set_relying_party_id", lambda _value: None)
    monkeypatch.setattr(subject, "set_authenticated_user_id", lambda _value: None)
    result = subject.run(_args(tmp_path, exports=[("legacy", one), ("legacy", two)], apply=True),
                         service_factory=lambda: FakeService(calls, fail_at=1))
    assert result["failedRecords"] == 1
    assert result["stopped"] is True
    assert len(calls) == 1


def test_cli_requires_apply_owner_and_explicit_input(tmp_path):
    with pytest.raises(SystemExit):
        subject.parse_args(["--root", str(tmp_path), "--apply", "--export-file", f"legacy={tmp_path / 'x.md'}"])
    with pytest.raises(SystemExit):
        subject.parse_args(["--root", str(tmp_path)])
