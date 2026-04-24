# Standard libraries
import hashlib
import dateutil.parser
import datetime
import logging
import re
from typing import List

logger = logging.getLogger(__name__)


def build_hash(link: str) -> str:
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


def convert_date_string_for_mysql(rss_date: str) -> str:
    try:
        date_object = dateutil.parser.parse(rss_date)
        date_created = datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')
    except dateutil.parser.ParserError as e:
        # We couldn't parse the date for some reason. Make it "now"
        logger.error(f'Could not parse date. Error:\n{e}')
        date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')
    return date_created


def convert_epoch_to_mysql(epoch: float) -> str:
    date_object = datetime.datetime.utcfromtimestamp(int(epoch))
    return datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')


def extract_urls_from_text(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r'https?://[^\s)\]>"\']+', text)


class Post:
    def __init__(self):
        self.source = ''
        self.author = ''
        self.description = ''
        self.direct_link = ''
        self.date_created = ''
        self.urls: List[str] = []
        self.unique_id_string = ''

    def add_url(self, url: str):
        if url and url not in self.urls:
            self.urls.append(url)

    def _generate_unique_url_string(self):
        sorted_urls = sorted(u for u in self.urls if u)
        self.unique_id_string = build_hash(','.join(sorted_urls))

    @property
    def post_has_urls(self) -> bool:
        return bool(self.urls)

    def get_post_row(self) -> tuple:
        # Row for the `posts` table; matches db_utils.db_insert column order.
        return (
            self.source,
            self.author,
            self.description,
            self.direct_link,
            self.date_created,
            self.unique_id_string,
        )

    def get_url_rows(self) -> List[tuple]:
        # (position, url, url_hash) -- post_id is filled in by db_utils after insert.
        return [(idx, url, build_hash(url)) for idx, url in enumerate(self.urls)]


class RssPost(Post):
    def __init__(self, feed_source, feed_author, post):
        super().__init__()
        self.extract_data_from_post(feed_source, feed_author, post)

    def extract_data_from_post(self, feed_source, feed_author, post):
        self.source = feed_source
        self.author = feed_author
        self.description = post.title
        self.direct_link = None
        self.add_url(post.link)

        if 'published' in post:
            self.date_created = convert_date_string_for_mysql(post.published)
        elif 'updated' in post:
            self.date_created = convert_date_string_for_mysql(post.updated)
        else:
            self.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')

        self._generate_unique_url_string()


class RedditPost(Post):
    def __init__(self, post):
        super().__init__()
        self.extract_data_from_post(post)
        self._extract_urls(post)
        self._generate_unique_url_string()

    def extract_data_from_post(self, post):
        self.source = f'https://www.reddit.com/{post["data"]["subreddit_name_prefixed"]}'
        self.author = post['data']['author']
        self.description = post['data']['title']
        self.direct_link = f'https://www.reddit.com{post["data"]["permalink"]}'
        self.date_created = convert_epoch_to_mysql(post['data']['created_utc'])

    def _extract_urls(self, post):
        if post['data'].get('url', False):
            url = post['data']['url']
            if not url.startswith('https://www.reddit.com/') and url != '':
                self.add_url(url)


class BlueskyPost(Post):
    def __init__(self, source: str, author: str, description: str, direct_link: str, created_at: str, urls: List[str]):
        super().__init__()
        self.source = source
        self.author = author
        self.description = description
        self.direct_link = direct_link
        self.date_created = convert_date_string_for_mysql(created_at)
        for url in urls:
            self.add_url(url)
        self._generate_unique_url_string()
