"""What a collection cycle produced, before anything is written down.

Source modules fetch and normalize, then hand back one of these. The collector
merges them and writes a single batch, which is what makes a cycle atomic: a
crash mid-Mastodon does not leave Reddit's posts committed and its checkpoint
lost, because nothing is committed until every source has had its turn.

The distinction that matters here is between a snapshot that was *not
collected* and one that is *empty*. Follows files replace an entire scope, so
"we did not sync follows this cycle" must not be written down as "the account
follows nobody". Absent means untouched; present-and-empty means observed.
"""

from dataclasses import dataclass

from .contract import to_timestamp, utc_now


@dataclass(frozen=True)
class FollowsSnapshot:
    """A complete follows list for one scope at one moment."""

    records: tuple
    observed_at: str
    scope: str | None = None

    @classmethod
    def create(cls, records, *, scope=None, observed_at=None) -> 'FollowsSnapshot':
        return cls(
            records=tuple(records),
            observed_at=to_timestamp(observed_at) if observed_at else utc_now(),
            scope=scope,
        )


class CollectionResult:
    """Accumulates normalized records from one or more sources."""

    def __init__(self):
        self.posts: list = []
        self.rss_observations: list = []
        self.checkpoints: list = []
        self.bluesky_follows: FollowsSnapshot | None = None
        self.mastodon_follows: dict[str, FollowsSnapshot] = {}

    def __repr__(self) -> str:
        return f'<CollectionResult {self.summary()}>'

    # --- accumulation -----------------------------------------------------

    def add_posts(self, records) -> None:
        self.posts.extend(records)

    def add_rss_observations(self, records) -> None:
        self.rss_observations.extend(records)

    def add_checkpoints(self, records) -> None:
        self.checkpoints.extend(records)

    def set_bluesky_follows(self, records, *, observed_at=None) -> None:
        self.bluesky_follows = FollowsSnapshot.create(records, observed_at=observed_at)

    def set_mastodon_follows(self, scope, records, *, observed_at=None) -> None:
        self.mastodon_follows[scope] = FollowsSnapshot.create(
            records, scope=scope, observed_at=observed_at
        )

    def extend(self, other: 'CollectionResult') -> 'CollectionResult':
        """Fold another source's result into this one."""
        self.posts.extend(other.posts)
        self.rss_observations.extend(other.rss_observations)
        self.checkpoints.extend(other.checkpoints)
        if other.bluesky_follows is not None:
            self.bluesky_follows = other.bluesky_follows
        self.mastodon_follows.update(other.mastodon_follows)
        return self

    # --- inspection -------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return not (
            self.posts
            or self.rss_observations
            or self.checkpoints
            or self.bluesky_follows is not None
            or self.mastodon_follows
        )

    def summary(self) -> dict:
        """Counts suitable for a single log line."""
        summary = {
            'posts': len(self.posts),
            'rss_observations': len(self.rss_observations),
            'checkpoints': len(self.checkpoints),
        }
        if self.bluesky_follows is not None:
            summary['bluesky_follows'] = len(self.bluesky_follows.records)
        for scope in sorted(self.mastodon_follows):
            summary[f'mastodon_follows[{scope}]'] = len(self.mastodon_follows[scope].records)
        return summary

    # --- output -----------------------------------------------------------

    def write_to(self, batch) -> None:
        """Write everything collected into an open batch.

        Only opens a file for a kind that has content, so an absent file in a
        batch is an honest statement that the collector observed nothing of
        that kind rather than a file it forgot to fill in.
        """
        if self.posts:
            batch.add_posts(self.posts)
        if self.rss_observations:
            batch.add_rss_observations(self.rss_observations)
        if self.checkpoints:
            batch.add_checkpoints(self.checkpoints)
        if self.bluesky_follows is not None:
            batch.set_bluesky_follows(
                self.bluesky_follows.records,
                observed_at=self.bluesky_follows.observed_at,
            )
        for scope in sorted(self.mastodon_follows):
            snapshot = self.mastodon_follows[scope]
            batch.set_mastodon_follows(
                scope, snapshot.records, observed_at=snapshot.observed_at
            )
