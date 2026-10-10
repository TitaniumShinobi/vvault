import json
import base64
import hashlib

import pytest

from vvault.etl.codex_desktop_adapter import (
    CodexSessionRejected,
    adapt_paths,
    build_thread_record,
    parse_segment_lines,
)


THREAD = "43e73643-dfd6-4958-90b1-8fd7e97c93c6"


def _line(value):
    return json.dumps(value, ensure_ascii=False)


def _segment(*, thread_source="user", source="vscode", timestamp="2026-01-01T00:00:00Z", text="hello\r\nworld"):
    return [
        _line({"type": "session_meta", "payload": {"id": THREAD, "timestamp": timestamp, "thread_source": thread_source, "source": source}}),
        _line({"timestamp": timestamp, "type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "secret system prompt"}]}}),
        _line({"timestamp": timestamp, "type": "response_item", "payload": {"type": "function_call", "name": "tool"}}),
        _line({"timestamp": timestamp, "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}),
        _line({"timestamp": timestamp, "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "reply"}]}}),
    ]


@pytest.mark.parametrize("surface", ["vscode", "cli"])
def test_accepts_only_proven_top_level_human_codex_surfaces(surface):
    segment = parse_segment_lines(_segment(source=surface))
    assert segment.thread_id == THREAD
    assert segment.surface == surface
    assert [message.role for message in segment.messages] == ["user", "assistant"]
    assert "secret system prompt" not in "".join(message.text for message in segment.messages)


@pytest.mark.parametrize("thread_source", ["guardian_review", "subagent", "automation", "agent_created_thread", None])
def test_rejects_non_human_and_ambiguous_thread_sources(thread_source):
    with pytest.raises(CodexSessionRejected):
        parse_segment_lines(_segment(thread_source=thread_source))


def test_rejects_structured_subagent_source_even_if_thread_source_claims_user():
    with pytest.raises(CodexSessionRejected):
        parse_segment_lines(_segment(source={"subagent": "worker"}))


def test_continuations_aggregate_by_stable_uuid_and_are_deterministic():
    later = parse_segment_lines(_segment(timestamp="2026-01-02T00:00:00Z", text="caf\u00e9"), source_name="b.jsonl")
    earlier = parse_segment_lines(_segment(timestamp="2026-01-01T00:00:00Z", text="cafe\u0301"), source_name="a.jsonl")
    one = build_thread_record([later, earlier])
    two = build_thread_record([earlier, later])
    assert one == two
    assert one["envelope"]["sourceThreadId"] == THREAD
    assert one["envelope"]["segmentCount"] == 2
    assert one["envelope"]["humanCount"] == 2
    assert one["envelope"]["classification"] == "ACCOUNT_PRIVATE"
    assert one["envelope"]["constructId"] == "zen-001"
    assert "cafe\u0301" not in one["projection"]["content"]
    assert "caf\u00e9" in one["projection"]["content"]


def test_projection_hash_and_size_cover_exact_utf8_content():
    record = build_thread_record([parse_segment_lines(_segment())])
    content = record["projection"]["content"].encode("utf-8")
    assert record["projection"]["utf8Bytes"] == len(content)
    assert record["projection"]["sha256"] == hashlib.sha256(content).hexdigest()


def test_adapt_paths_skips_rejected_sessions_and_groups_continuations(tmp_path):
    accepted_a = tmp_path / "accepted-a.jsonl"
    accepted_b = tmp_path / "accepted-b.jsonl"
    rejected = tmp_path / "rejected.jsonl"
    accepted_a.write_text("\n".join(_segment(timestamp="2026-01-01T00:00:00Z")), encoding="utf-8")
    accepted_b.write_text("\n".join(_segment(timestamp="2026-01-02T00:00:00Z")), encoding="utf-8")
    rejected.write_text("\n".join(_segment(thread_source="automation")), encoding="utf-8")
    records = adapt_paths([rejected, accepted_b, accepted_a])
    assert len(records) == 1
    assert records[0]["envelope"]["segmentCount"] == 2


def test_invalid_uuid_is_rejected():
    lines = _segment()
    meta = json.loads(lines[0])
    meta["payload"]["id"] = "not-a-uuid"
    lines[0] = _line(meta)
    with pytest.raises(CodexSessionRejected):
        parse_segment_lines(lines)


def test_source_native_container_recovers_exact_segment_bytes_without_paths(tmp_path):
    source = tmp_path / "private-location" / "rollout.jsonl"
    source.parent.mkdir()
    exact = ("\n".join(_segment()) + "\n\n").encode("utf-8")
    source.write_bytes(exact)
    record = adapt_paths([source])[0]
    container_bytes = base64.b64decode(record["sourceNative"]["bytesBase64"])
    assert hashlib.sha256(container_bytes).hexdigest() == record["sourceNative"]["sha256"]
    assert len(container_bytes) == record["sourceNative"]["byteLength"]
    container = json.loads(container_bytes)
    segment = container["segments"][0]
    assert base64.b64decode(segment["bytesBase64"]) == exact
    assert segment["sha256"] == hashlib.sha256(exact).hexdigest()
    assert segment["byteLength"] == len(exact)
    assert segment["name"] == "rollout.jsonl"
    assert str(tmp_path) not in container_bytes.decode("utf-8")


def test_changed_raw_bytes_change_source_native_hash_but_not_projection(tmp_path):
    source = tmp_path / "rollout.jsonl"
    common = "\n".join(_segment()).encode("utf-8")
    source.write_bytes(common)
    first_record = adapt_paths([source])[0]
    source.write_bytes(common + b"\n")
    second_record = adapt_paths([source])[0]
    assert first_record["projection"]["sha256"] == second_record["projection"]["sha256"]
    assert first_record["sourceNative"]["sha256"] != second_record["sourceNative"]["sha256"]
