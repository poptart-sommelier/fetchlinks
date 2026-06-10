import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_setup


class DbSetupTests(unittest.TestCase):
    def test_db_initial_setup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_dir = Path(tmp_dir) / "db"
            db_name = "fetchlinks.db"
            db_path = db_dir / db_name

            db_setup.db_initial_setup(db_path)
            db_setup.db_initial_setup(db_path)

            self.assertTrue(db_path.exists())

            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

            self.assertIn("posts", tables)
            self.assertIn("post_urls", tables)

    def test_rss_feed_health_table_created(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "db" / "fetchlinks.db"

            db_setup.db_initial_setup(db_path)

            with sqlite3.connect(db_path) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(rss_feed_health)")
                }

            self.assertEqual(
                columns,
                {
                    "normalized_url",
                    "last_fetched_at",
                    "last_success_at",
                    "last_status",
                    "last_error",
                    "consecutive_failures",
                    "etag",
                    "last_modified",
                    "latest_entry_at",
                    "site_link",
                },
            )

    def test_backfill_rss_feed_health_copies_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "db" / "fetchlinks.db"
            db_setup.db_initial_setup(db_path)

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO rss_feeds "
                    "(feed_url, normalized_url, enabled, added_at, "
                    " last_status, consecutive_failures, etag, site_link) "
                    "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
                    (
                        "https://example.com/feed",
                        "https://example.com/feed",
                        "2026-06-10 00:00:00",
                        200,
                        3,
                        '"abc"',
                        "https://example.com",
                    ),
                )
                conn.commit()

                first = db_setup.backfill_rss_feed_health(conn)
                second = db_setup.backfill_rss_feed_health(conn)
                conn.commit()

                row = conn.execute(
                    "SELECT last_status, consecutive_failures, etag, site_link "
                    "FROM rss_feed_health WHERE normalized_url = ?",
                    ("https://example.com/feed",),
                ).fetchone()

            self.assertEqual(first, 1)
            self.assertEqual(second, 0)
            self.assertEqual(row, (200, 3, '"abc"', "https://example.com"))


if __name__ == "__main__":
    unittest.main()
