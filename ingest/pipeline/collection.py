"""What a collection cycle produced, before anything is written down.

Source modules fetch and normalize, then hand back one of these. The collector
merges them and writes a single batch, which is what makes a cycle atomic:
nothing is committed until every source has had its turn, so a failure while
writing cannot leave Reddit's posts committed and its checkpoint lost.

The distinction that matters here is between a snapshot that was *not
collected* and one that is *empty*. Follows files replace an entire scope, so
"we did not sync follows this cycle" must not be written down as "the account
follows nobody". Absent means untouched; present-and-empty means observed.
"""

from dataclasses import dataclass

from .contract import (
    ERROR_KIND_NONE,
    RESULT_FAILED,
    RESULT_OK,
    RESULT_PARTIAL,
    RESULT_SKIPPED,
    SourceReport,
    SubtaskReport,
    clean_error_kind,
    clean_error_message,
    to_timestamp,
    utc_now,
)


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


class SourceTally:
    """What one source did, counted as it happens.

    Sources are asked to say so rather than having their health guessed from
    how many posts came back. Zero posts is an ordinary quiet half hour, and
    the sources disagree about how they signal trouble: RSS returns an
    observation per feed, Reddit and Mastodon log an error and hand back an
    empty list, Bluesky raises. Only the source itself knows which happened.
    """

    def __init__(self, source_type: str):
        self.source_type = source_type
        self.channels_attempted = 0
        self.channels_succeeded = 0
        self.channels_failed = 0
        self.items_returned = 0
        self.posts_kept = 0
        self.errors: dict[str, int] = {}
        self.error_kind = ERROR_KIND_NONE
        self.error_message = ''
        self.skipped = False
        self.elapsed_ms = 0

    def __repr__(self) -> str:
        return (f'<SourceTally {self.source_type} '
                f'{self.channels_succeeded}/{self.channels_attempted} ok>')

    # --- recording ---------------------------------------------------------

    def channel_succeeded(self, items: int = 0) -> None:
        self.channels_attempted += 1
        self.channels_succeeded += 1
        self.items_returned += max(0, int(items))

    def channel_failed(self, kind: str, message: str = '', items: int = 0) -> None:
        self.channels_attempted += 1
        self.channels_failed += 1
        self.items_returned += max(0, int(items))
        self.fault(kind, message)

    def fault(self, kind: str, message: str = '') -> None:
        """Note a failure without it being a whole channel's worth.

        Also used for the failure that takes a source out before it reaches
        any channel at all, such as a login that will not complete.
        """
        clean = clean_error_kind(kind)
        self.errors[clean] = self.errors.get(clean, 0) + 1
        # Latest wins. One message is enough to start looking in the right
        # place, and the full text is in the collector's own log anyway.
        self.error_kind = clean
        self.error_message = clean_error_message(message)

    @property
    def fault_count(self) -> int:
        return sum(self.errors.values())

    def close_channel(self, faults_before: int, items: int = 0) -> None:
        """Finish a channel that reports trouble by logging rather than raising.

        Mastodon and its like hand back an empty page whether the instance was
        quiet or unreachable, so the only honest signal is whether anything was
        noted while the channel was open.
        """
        if self.fault_count > faults_before:
            self.channels_attempted += 1
            self.channels_failed += 1
            self.items_returned += max(0, int(items))
        else:
            self.channel_succeeded(items=items)

    def merge(self, other: 'SourceTally') -> None:
        self.channels_attempted += other.channels_attempted
        self.channels_succeeded += other.channels_succeeded
        self.channels_failed += other.channels_failed
        self.items_returned += other.items_returned
        self.posts_kept += other.posts_kept
        self.elapsed_ms += other.elapsed_ms
        for kind, count in other.errors.items():
            self.errors[kind] = self.errors.get(kind, 0) + count
        if other.error_kind:
            self.error_kind = other.error_kind
            self.error_message = other.error_message
        self.skipped = self.skipped and other.skipped

    # --- output ------------------------------------------------------------

    @property
    def result(self) -> str:
        if self.skipped:
            return RESULT_SKIPPED
        if self.error_kind and not self.channels_attempted:
            return RESULT_FAILED
        if not self.channels_failed:
            return RESULT_OK
        if self.channels_succeeded:
            return RESULT_PARTIAL
        return RESULT_FAILED

    def to_report(self, *, elapsed_ms: int | None = None, result: str | None = None) -> SourceReport:
        return SourceReport(
            source_type=self.source_type,
            result=result or self.result,
            elapsed_ms=self.elapsed_ms if elapsed_ms is None else elapsed_ms,
            channels_attempted=self.channels_attempted,
            channels_succeeded=self.channels_succeeded,
            channels_failed=self.channels_failed,
            items_returned=self.items_returned,
            posts_kept=self.posts_kept,
            errors=dict(self.errors),
            error_kind=self.error_kind,
            error_message=self.error_message,
        )


@dataclass(frozen=True)
class Subtask:
    """Work done alongside a source that can fail without failing the source.

    A follows snapshot is the case in point: it can fail while every post
    still arrives, and reporting that as a broken source would send the reader
    looking in the wrong place.
    """

    name: str
    scope: str = ''
    result: str = RESULT_OK
    elapsed_ms: int = 0
    error_kind: str = ERROR_KIND_NONE
    error_message: str = ''

    def to_report(self) -> SubtaskReport:
        return SubtaskReport(
            name=self.name,
            scope=self.scope,
            result=self.result,
            elapsed_ms=self.elapsed_ms,
            error_kind=self.error_kind,
            error_message=self.error_message,
        )


class CollectionResult:
    """Accumulates normalized records from one or more sources."""

    def __init__(self):
        self.posts: list = []
        self.rss_observations: list = []
        self.checkpoints: list = []
        self.bluesky_follows: FollowsSnapshot | None = None
        self.mastodon_follows: dict[str, FollowsSnapshot] = {}
        self.failed_sources: list[str] = []
        self.attempted_sources: list[str] = []
        self.tallies: dict[str, SourceTally] = {}
        self.subtasks: list[Subtask] = []

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

    def record_failure(self, name: str) -> None:
        """Note that a source failed, so the cycle can report an honest total.

        Deliberately not part of `is_empty`: a cycle where everything failed
        has nothing of its own to write down. What records the failure is the
        run summary, which travels in the batch alongside the content.
        """
        self.failed_sources.append(name)

    def record_attempt(self, name: str) -> None:
        self.attempted_sources.append(name)

    @property
    def every_source_failed(self) -> bool:
        """True when something was tried and none of it worked.

        The usual cause is the machine itself having no network, which is
        worth telling apart from a quiet half hour.
        """
        return bool(self.attempted_sources) and \
            len(self.failed_sources) == len(self.attempted_sources)

    def tally(self, source_type: str) -> SourceTally:
        """The counter for one source, created on first use."""
        existing = self.tallies.get(source_type)
        if existing is None:
            existing = SourceTally(source_type)
            self.tallies[source_type] = existing
        return existing

    def record_subtask(self, subtask: Subtask) -> None:
        self.subtasks.append(subtask)

    def extend(self, other: 'CollectionResult') -> 'CollectionResult':
        """Fold another source's result into this one."""
        self.posts.extend(other.posts)
        self.rss_observations.extend(other.rss_observations)
        self.checkpoints.extend(other.checkpoints)
        if other.bluesky_follows is not None:
            self.bluesky_follows = other.bluesky_follows
        self.mastodon_follows.update(other.mastodon_follows)
        self.failed_sources.extend(other.failed_sources)
        self.attempted_sources.extend(other.attempted_sources)
        for source_type, tally in other.tallies.items():
            self.tally(source_type).merge(tally)
        self.subtasks.extend(other.subtasks)
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
        if self.failed_sources:
            summary['failed'] = ','.join(self.failed_sources)
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
