import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_data.py"
SPEC = importlib.util.spec_from_file_location("update_data", SCRIPT_PATH)
update_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_data)


class UpdateDataTests(unittest.TestCase):
    def test_date_parser_handles_trip_dates_and_rejects_past_or_invalid_dates(self):
        today = datetime(2026, 10, 26, tzinfo=timezone.utc).date()
        self.assertEqual(update_data.event_dates("Manifestation Paris on October 31", "", today), ["2026-10-31"])
        self.assertEqual(update_data.event_dates("Grève à Paris le 5 novembre", "", today), ["2026-11-05"])
        self.assertEqual(update_data.event_dates("Protest Paris le 05/11", "", today), ["2026-11-05"])
        self.assertEqual(update_data.event_dates("Protest Paris on 11/05", "", today), ["2026-11-05"])
        self.assertEqual(update_data.event_dates("Protest Paris on 11/05/2026", "", today), ["2026-11-05"])
        self.assertEqual(update_data.event_dates("Protest Paris 11/05", "", today), [])
        self.assertEqual(update_data.event_dates("Manifestation le 31 octobre", "", today), ["2026-10-31"])
        self.assertEqual(update_data.event_dates("Manifestation le 5 janvier", "", today), [])
        self.assertEqual(update_data.event_dates("Manifestation Paris le 31 février 2026", "", today), [])
        self.assertEqual(update_data.event_dates("Manifestation Paris le 12 octobre 2026", "", today), [])

    def test_date_parser_handles_explicit_and_inferred_year_across_new_year(self):
        today = datetime(2026, 12, 20, tzinfo=timezone.utc).date()
        self.assertEqual(update_data.event_dates("Manifestation Paris le 5 janvier", "2026-10-01T00:00:00Z", today), ["2027-01-05"])
        self.assertEqual(update_data.event_dates("Manifestation Paris le 2027-01-05", "", today), ["2027-01-05"])
        self.assertEqual(update_data.event_dates("Manifestation Paris le 05/01/2027", "", today), ["2027-01-05"])
        self.assertEqual(update_data.event_dates("Manifestation Paris on 01/05/2027", "", today), ["2027-01-05"])

    def test_feed_queries_cover_year_boundary_for_full_event_window(self):
        feeds = {feed["id"]: feed for feed in update_data.build_feeds(datetime(2026, 12, 20, tzinfo=timezone.utc))}
        self.assertIn('"January 2027"', feeds["action_dates"]["query"])
        self.assertIn('"janvier 2027"', feeds["action_dates"]["query"])
        self.assertIn('"February 2027"', feeds["action_dates"]["query"])
        self.assertNotIn('"March 2027"', feeds["action_dates"]["query"])
        self.assertIn("venue_impacts", feeds)
        self.assertIn("louvre.fr", feeds["venue_impacts"]["query"])

    def test_event_aggregation_deduplicates_titles_and_labels_unverified_fields(self):
        generated_at = "2026-10-26T12:00:00Z"
        title = "Manifestation Paris October 31 route and 14:00 in 10th arrondissement"
        feeds = [
            {"id": "news", "ok": True, "checkedAt": generated_at, "items": [
                {"id": "one", "title": title, "link": "https://news.example/a", "source": "Media", "publishedAt": generated_at, "publisherDomainMatchesOfficial": False},
                {"id": "two", "title": title.upper(), "link": "https://news.example/b", "source": "Media mirror", "publishedAt": generated_at, "publisherDomainMatchesOfficial": False},
            ]},
            {"id": "action_routes", "ok": True, "checkedAt": generated_at, "items": [
                {"id": "official", "title": "Manifestation Paris October 31 itinéraire 14:00 Paris 10e", "link": "https://prefecturedepolice.interieur.gouv.fr/notice", "source": "Préfecture", "publishedAt": generated_at, "publisherDomainMatchesOfficial": True},
            ]},
        ]
        events = update_data.derive_events(feeds, generated_at)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["date"], "2026-10-31")
        self.assertFalse("2026-10-31" >= "2026-11-03" and "2026-10-31" <= "2026-11-09")
        self.assertEqual(len(event["sources"]), 2)
        self.assertIn("尚未核验原文", event["route"]["status"])
        self.assertTrue(event["route"]["evidence"][0]["officialDomainMatch"])
        self.assertNotEqual(event["meetingTime"]["status"], "待核实")
        self.assertNotEqual(event["districts"]["status"], "待核实")
        self.assertEqual(event["transport"]["status"], "待核实")
        self.assertIn("待核实", event["status"])

    def test_trip_monitor_has_all_days_and_focuses_november_5(self):
        generated_at = "2026-10-26T12:00:00Z"
        feeds = [
            {"id": "action_dates", "label": "日期", "ok": True, "checkedAt": generated_at, "items": [
                {"id": "strike", "title": "Strike Paris November 5", "description": "", "link": "https://news.example/strike", "source": "Media", "publishedAt": generated_at, "publisherDomainMatchesOfficial": False},
            ]},
            {"id": "venue_impacts", "label": "景点", "ok": True, "checkedAt": generated_at, "items": [
                {"id": "louvre", "title": "Louvre fermé exceptionnellement November 5", "description": "", "link": "https://www.louvre.fr/notice", "source": "Louvre", "publishedAt": generated_at, "publisherDomainMatchesOfficial": True},
            ]},
            {"id": "transport", "label": "交通", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "action_impacts", "label": "交通影响", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "police", "label": "警方", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "action_routes", "label": "路线", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "news", "label": "新闻", "ok": True, "checkedAt": generated_at, "items": []},
        ]
        events = update_data.derive_events(feeds, generated_at)
        monitor = update_data.derive_trip_monitor(feeds, events, generated_at)
        self.assertEqual(len(monitor["days"]), 7)
        focus = next(day for day in monitor["days"] if day["date"] == "2026-11-05")
        self.assertTrue(focus["focus"])
        self.assertEqual(focus["collective"]["status"], "有集体游行/罢工日期线索")
        self.assertIn("卢浮宫", focus["venues"]["watchList"])
        self.assertIn("Louvre", focus["venues"]["evidence"][0]["title"])

    def test_trip_monitor_marks_failed_venue_source_as_unknown(self):
        generated_at = "2026-10-26T12:00:00Z"
        monitor = update_data.derive_trip_monitor([
            {"id": "police", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "action_dates", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "action_routes", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "news", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "transport", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "action_impacts", "ok": True, "checkedAt": generated_at, "items": []},
            {"id": "venue_impacts", "ok": False, "checkedAt": generated_at, "lastSuccessAt": "2026-10-25T12:00:00Z", "items": []},
        ], [], generated_at)
        focus = next(day for day in monitor["days"] if day["date"] == "2026-11-05")
        self.assertEqual(focus["venues"]["status"], "来源刷新失败，无法判断")

    def test_generic_gathering_title_does_not_claim_a_route(self):
        generated_at = "2026-10-26T12:00:00Z"
        event = update_data.derive_events([{"id": "action_routes", "ok": True, "checkedAt": generated_at, "items": [
            {"id": "gathering", "title": "Manifestation Paris October 31 rassemblement", "link": "https://prefecturedepolice.interieur.gouv.fr/notice", "source": "Préfecture", "publishedAt": generated_at, "publisherDomainMatchesOfficial": True},
        ]}], generated_at)[0]
        self.assertEqual(event["route"]["status"], "待核实")

    def test_failed_feed_cached_events_are_marked_stale(self):
        generated_at = "2026-10-26T12:00:00Z"
        feeds = [{"id": "news", "ok": False, "checkedAt": generated_at, "lastSuccessAt": "2026-10-25T12:00:00Z", "items": [
            {"id": "cached", "title": "Manifestation Paris October 31", "description": "RATP métro closed", "link": "https://news.example/a", "source": "Media", "publishedAt": generated_at, "publisherDomainMatchesOfficial": False},
        ]}]
        event = update_data.derive_events(feeds, generated_at)[0]
        self.assertFalse(event["sources"][0]["currentFeedSuccess"])
        self.assertEqual(event["sources"][0]["lastSuccessAt"], "2026-10-25T12:00:00Z")
        self.assertEqual(event["transport"]["evidence"][0]["lastSuccessAt"], "2026-10-25T12:00:00Z")

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
            self.assertEqual(payload["refreshAfterSeconds"], 900)


if __name__ == "__main__":
    unittest.main()
