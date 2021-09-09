import hashlib
import dateutil.parser
import datetime
import logging

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


class Post:
    def __init__(self):
        self.source = ''
        self.author = ''
        self.description = ''
        self.direct_link = ''
        self.urls = []
        self.date_created = ''
        self.unique_id_string = ''
        self.url_1 = ''
        self.url_2 = ''
        self.url_3 = ''
        self.url_4 = ''
        self.url_5 = ''
        self.url_6 = ''
        self.urls_missing = 0

    def prep_for_db(self):
        # break out urls to our url fields (max 6), warn if we have more than 6.
        for i, url in enumerate(self.urls):
            if i > 5:
                self.urls_missing = 1
                break

            if i == 0:
                self.url_1 = url['unshort_url'] if url['unshort_url'] is not None else url['url']
            if i == 1:
                self.url_2 = url['unshort_url'] if url['unshort_url'] is not None else url['url']
            if i == 2:
                self.url_3 = url['unshort_url'] if url['unshort_url'] is not None else url['url']
            if i == 3:
                self.url_4 = url['unshort_url'] if url['unshort_url'] is not None else url['url']
            if i == 4:
                self.url_5 = url['unshort_url'] if url['unshort_url'] is not None else url['url']
            if i == 5:
                self.url_6 = url['unshort_url'] if url['unshort_url'] is not None else url['url']


class RssPost(Post):
    def __init__(self, feed_source, feed_author, post):
        super().__init__()
        self.extract_data_from_post(feed_source, feed_author, post)

    def extract_data_from_post(self, feed_source, feed_author, post):
        self.source = feed_source
        self.author = feed_author
        self.description = post.title
        self.direct_link = None
        self.urls = [{'url': post.link,
                      'unshort_url': None,
                      'unique_id': build_hash(post.link),
                      'unshort_unique_id': None}]

        if 'published' in post:
            self.date_created = convert_date_string_for_mysql(post.published)
        elif 'updated' in post:
            self.date_created = convert_date_string_for_mysql(post.updated)
        else:
            self.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')

        self.unique_id_string = ','.join(sorted([url['unique_id'] for url in self.urls]))


class RedditPost(Post):
    def __init__(self, post):
        super().__init__()
        self.extract_data_from_post(post)
        self.parse_urls(post)
        self.unique_id_string = ','.join([url['unique_id'] for url in self.urls])

    def extract_data_from_post(self, post):
        self.source = f'https://www.reddit.com/{post["data"]["subreddit_name_prefixed"]}'
        self.author = post['data']['author']
        self.description = post['data']['title']
        self.direct_link = f'https://www.reddit.com{post["data"]["permalink"]}'
        self.urls = list()
        self.date_created = convert_epoch_to_mysql(post['data']['created_utc'])

    def parse_urls(self, post):
        if post['data'].get('url', False):
            url = post['data']['url']
            if not url.startswith('https://www.reddit.com/') and url != '':
                self.urls = [{'url': url,
                              'unshort_url': None,
                              'unique_id': build_hash(url),
                              'unshort_unique_id': None}]


class TwitterPost(Post):
    def __init__(self, post):
        super().__init__()
        pass
