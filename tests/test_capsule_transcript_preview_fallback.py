import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from vvault.server import vvault_web_server as server


class _FakeTranscriptDetailQuery:
    def __init__(self, rows_by_id):
        self._rows_by_id = rows_by_id
        self._row_id = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        if field == "id":
            self._row_id = value
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows_by_id.get(self._row_id))


class _FakeTranscriptSupabaseClient:
    def __init__(self, rows_by_id):
        self._rows_by_id = rows_by_id

    def table(self, _name):
        return _FakeTranscriptDetailQuery(self._rows_by_id)


class TestCapsuleTranscriptPreviewFallback(unittest.TestCase):
    def test_build_capsule_preview_from_transcripts_returns_structured_sessions(self):
        transcript_rows = [
            {
                "id": "row-1",
                "filename": "instances/nova-001/chatgpt/2025/December/day-after-christmas.txt",
                "content": "\n".join(
                    [
                        "You said:",
                        "Do you carry continuity from the previous thread?",
                        "ChatGPT said:",
                        "Yes. I can carry continuity without pretending omniscience.",
                    ]
                ),
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        rows_by_id = {
            "row-1": {
                "id": "row-1",
                "filename": "instances/nova-001/chatgpt/2025/December/day-after-christmas.txt",
                "content": "\n".join(
                    [
                        "You said:",
                        "Do you carry continuity from the previous thread?",
                        "ChatGPT said:",
                        "Yes. I can carry continuity without pretending omniscience.",
                    ]
                ),
                "created_at": "2026-01-01T00:00:00Z",
            }
        }

        with patch.object(server, "_query_transcript_rows_for_preview", return_value=transcript_rows), patch.object(
            server, "supabase_client", _FakeTranscriptSupabaseClient(rows_by_id)
        ):
            preview = server._build_capsule_preview_from_transcripts("nova-001")

        payload = json.loads(preview)
        self.assertTrue(payload["preview_only"])
        self.assertEqual(payload["summary"]["total_sessions"], 1)
        self.assertEqual(payload["sessions"][0]["source"], "ChatGPT")

    def test_build_capsule_preview_from_transcripts_returns_degraded_preview_when_unparseable(self):
        transcript_rows = [
            {
                "id": "row-2",
                "filename": "instances/nova-001/chatgpt/2025/December/unstructured-log.txt",
                "content": "This is readable transcript material without explicit speaker markers. " * 4,
                "created_at": "2026-01-02T00:00:00Z",
            }
        ]
        rows_by_id = {
            "row-2": {
                "id": "row-2",
                "filename": "instances/nova-001/chatgpt/2025/December/unstructured-log.txt",
                "content": "This is readable transcript material without explicit speaker markers. " * 4,
                "created_at": "2026-01-02T00:00:00Z",
            }
        }

        with patch.object(server, "_query_transcript_rows_for_preview", return_value=transcript_rows), patch.object(
            server, "supabase_client", _FakeTranscriptSupabaseClient(rows_by_id)
        ):
            preview = server._build_capsule_preview_from_transcripts("nova-001")

        payload = json.loads(preview)
        self.assertTrue(payload["preview_only"])
        self.assertTrue(payload["preview_degraded"])
        self.assertEqual(payload["summary"]["reason"], "continuity_parser_returned_no_entries")
        self.assertEqual(payload["transcript_previews"][0]["source"], "ChatGPT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
