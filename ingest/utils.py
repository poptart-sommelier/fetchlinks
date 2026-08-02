# Standard libraries
import hashlib
import dateutil.parser
import datetime
from datetime import UTC
import logging
import re
from typing import List
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger(__name__)


def build_hash(link: str) -> str:
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def convert_date_string_for_mysql(rss_date: str) -> str:
    try:
        date_object = dateutil.parser.parse(rss_date)
        # If the source includes a tz offset, convert to UTC before dropping
        # tzinfo. Otherwise a feed dated "2026-05-22T20:00:00+09:00" would be
        # stored as "2026-05-22 20:00:00" and sort 9 hours into our future.
        if date_object.tzinfo is not None:
            date_object = date_object.astimezone(UTC).replace(tzinfo=None)
        date_created = datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')
    except dateutil.parser.ParserError as e:
        # We couldn't parse the date for some reason. Make it "now" (UTC)
        logger.error('Could not parse date. Error:\n%s', e)
        date_created = datetime.datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    return date_created


def convert_epoch_to_mysql(epoch: float) -> str:
    date_object = datetime.datetime.fromtimestamp(int(epoch), tz=UTC)
    return date_object.strftime('%Y-%m-%d %H:%M:%S')


# Allow a small clock-skew tolerance so legitimately-just-published items
# don't trigger a warning every time the publisher's clock is a couple of
# seconds ahead of ours.
_FUTURE_DATE_TOLERANCE_SECONDS = 60


def clamp_date_not_in_future(date_str: str) -> str:
    """Return ``date_str`` clamped to ``now()`` if it is in the future.

    Some sources legitimately stamp posts with future dates (scheduled
    webinars, conference announcements, publisher clock skew). Those rows
    sort wrong in any "most recent" view, so we cap the stored timestamp
    at the ingest time. The original date is preserved in the post body /
    description; only the sortable column changes.
    """
    if not date_str:
        return date_str
    try:
        parsed = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return date_str
    now = datetime.datetime.now(UTC).replace(tzinfo=None)
    if parsed <= now + datetime.timedelta(seconds=_FUTURE_DATE_TOLERANCE_SECONDS):
        return date_str
    logger.warning('Clamped future post date %s -> %s',
                   date_str, now.strftime('%Y-%m-%d %H:%M:%S'))
    return now.strftime('%Y-%m-%d %H:%M:%S')


def extract_urls_from_text(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r'https?://[^\s)\]>"\']+', text)


def normalize_url(url: str, base: str = '') -> str:
    """Normalize a candidate URL or return '' if it can't be made valid.

    - Strips whitespace.
    - Resolves protocol-relative ('//host/path') and site-relative ('/path')
      URLs against `base` when provided.
    - Rejects anything whose final scheme isn't http/https or that has no host.
    """
    if not url:
        return ''
    url = url.strip()
    if not url:
        return ''

    # Resolve relative forms against the feed/site base when we have one.
    if base and (url.startswith('//') or url.startswith('/') or not urlsplit(url).scheme):
        url = urljoin(base, url)

    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        return ''
    return url


class Post:
    def __init__(self):
        self.source = ''
        self.source_type = ''
        self.author = ''
        self.description = ''
        self.direct_link = ''
        self.date_created = ''
        self.urls: List[str] = []
        self.unique_id_string = ''

    def add_url(self, url: str, base: str = ''):
        cleaned = normalize_url(url, base)
        if cleaned and cleaned not in self.urls:
            self.urls.append(cleaned)

    def _generate_unique_url_string(self):
        sorted_urls = sorted(u for u in self.urls if u)
        self.unique_id_string = build_hash(','.join(sorted_urls))

    @property
    def post_has_urls(self) -> bool:
        return bool(self.urls)

    def to_record(self):
        """Convert to the destination-independent contract record.

        Clamps a future post date so a post means the same thing whichever way
        it is written out. Positions and URL
        hashes are deliberately not included: they are derivable from the
        ordered list, and a publisher that recomputes them cannot inherit a
        stale hash from an old collector.
        """
        from pipeline.contract import PostRecord

        return PostRecord(
            unique_id=self.unique_id_string,
            source=self.source,
            source_type=self.source_type,
            author=self.author,
            description=self.description,
            direct_link=self.direct_link,
            posted_at=clamp_date_not_in_future(self.date_created),
            urls=list(self.urls),
        )


class RssPost(Post):
    def __init__(self, feed_source, feed_author, post, site_link=None):
        super().__init__()
        self.extract_data_from_post(feed_source, feed_author, post, site_link)

    def extract_data_from_post(self, feed_source, feed_author, post, site_link=None):
        # Prefer the feed's advertised website (site_link) as the post's
        # source so the public UI can link "all posts from this feed"
        # without exposing the feed XML URL. Fall back to the feed URL
        # when site_link is unknown.
        self.source = site_link or feed_source
        self.source_type = 'rss'
        self.author = feed_author
        self.description = post.get('title', '')
        self.direct_link = ''
        # Resolve relative <link> values against the feed's site URL.
        self.add_url(post.get('link', ''), base=feed_source)

        if 'published' in post:
            self.date_created = convert_date_string_for_mysql(post.published)
        elif 'updated' in post:
            self.date_created = convert_date_string_for_mysql(post.updated)
        else:
            self.date_created = datetime.datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')

        self._generate_unique_url_string()


class RedditPost(Post):
    def __init__(self, post):
        super().__init__()
        self.extract_data_from_post(post)
        self._extract_urls(post)
        self._generate_unique_url_string()

    def extract_data_from_post(self, post):
        self.source = f'https://www.reddit.com/{post["data"]["subreddit_name_prefixed"]}'
        self.source_type = 'reddit'
        self.author = post['data']['author']
        self.description = post['data']['title']
        self.direct_link = f'https://www.reddit.com{post["data"]["permalink"]}'
        self.date_created = convert_epoch_to_mysql(post['data']['created_utc'])

    def _extract_urls(self, post):
        if post['data'].get('url'):
            url = post['data']['url']
            if not url.startswith(('http://', 'https://')):
                return
            if url.startswith('https://www.reddit.com/'):
                return
            self.add_url(url)


class BlueskyPost(Post):
    def __init__(self, source: str, author: str, description: str, direct_link: str, created_at: str, urls: List[str]):
        super().__init__()
        self.source = source
        self.source_type = 'bluesky'
        self.author = author
        self.description = description
        self.direct_link = direct_link
        self.date_created = convert_date_string_for_mysql(created_at)
        for url in urls:
            self.add_url(url)
        self._generate_unique_url_string()


class MastodonPost(Post):
    def __init__(self, source: str, author: str, description: str, direct_link: str, created_at: str, urls: List[str]):
        super().__init__()
        self.source = source
        self.source_type = 'mastodon'
        self.author = author
        self.description = description
        self.direct_link = direct_link
        self.date_created = convert_date_string_for_mysql(created_at)
        for url in urls:
            self.add_url(url)
        self._generate_unique_url_string()
