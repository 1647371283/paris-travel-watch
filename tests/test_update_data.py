import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_data.py"
SPEC = importlib.util.spec_from_file_location("update_data", SCRIPT_PATH)
update_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_data)


class UpdateDataTests(unittest.TestCase):
    def test_failed_feed_preserves_last_success_and_marks_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site"
            site.mkdir()
            output = site / "data.json"
            output.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {
                                "id": "police",
                                "label": "警方公告检索线索",
                                "items": [{"id": "saved-item", "title": "Prior result"}],
                                "ok": True,
                                "checkedAt": "2026-10-26T02:00:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(update_data, "ROOT", Path(directory)), patch.object(update_data, "SITE", site), patch.object(update_data, "OUTPUT", output), patch.object(update_data, "fetch_feed", side_effect=TimeoutError):
                update_data.main()

            payload = json.loads(output.read_text(encoding="utf-8"))
            police = next(feed for feed in payload["feeds"] if feed["id"] == "police")
            self.assertFalse(police["ok"])
            self.assertEqual(police["items"][0]["id"], "saved-item")
            self.assertEqual(police["lastSuccessAt"], "2026-10-26T02:00:00Z")
            self.assertIn("刷新失败", police["error"])
            self.assertTrue(payload["staticSnapshot"])
            self.assertEqual(payload["refreshAfterSeconds"], 1800)


if __name__ == "__main__":
    unittest.main()
