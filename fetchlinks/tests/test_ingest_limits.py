from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

import ingest_limits


class IngestLimitsTests(unittest.TestCase):
    def test_default_max_post_age_months_from_sources(self):
        self.assertEqual(
            ingest_limits.max_post_age_months_from_sources({}),
            ingest_limits.DEFAULT_MAX_POST_AGE_MONTHS,
        )

    def test_configured_max_post_age_months_from_sources(self):
        sources = {'ingest': {'max_post_age_months': 6}}

        self.assertEqual(ingest_limits.max_post_age_months_from_sources(sources), 6)

    def test_filter_keeps_posts_on_cutoff_boundary(self):
        posts = [SimpleNamespace(date_created='2026-01-26 12:00:00')]

        filtered = ingest_limits.filter_posts_by_age(
            posts,
            3,
            'test',
            now=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(filtered, posts)

    def test_filter_skips_posts_older_than_limit(self):
        old_post = SimpleNamespace(date_created='2026-01-25 12:00:00')
        recent_post = SimpleNamespace(date_created='2026-04-01 12:00:00')

        filtered = ingest_limits.filter_posts_by_age(
            [old_post, recent_post],
            3,
            'test',
            now=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(filtered, [recent_post])

    def test_filter_keeps_posts_with_missing_or_unparseable_dates(self):
        missing_date = SimpleNamespace(date_created='')
        bad_date = SimpleNamespace(date_created='not a date')

        filtered = ingest_limits.filter_posts_by_age(
            [missing_date, bad_date],
            3,
            'test',
            now=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
        )

        self.assertEqual(filtered, [missing_date, bad_date])


if __name__ == '__main__':
    unittest.main()
