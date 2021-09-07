import hashlib
from dateutil.parser import parse
import datetime

def build_hash(link):
    sha256_hash = hashlib.sha256(link.encode())
    return sha256_hash.hexdigest()


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

    def convert_date_rss_to_mysql(self, rss_date):
        try:
            date_object = parse(rss_date)
            self.date_created = datetime.datetime.strftime(date_object, '%Y-%m-%d %H:%M:%S')
        except Exception as e:
            # We couldn't parse the date for some reason. Make it "now"
            self.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')

    def extract_data_from_post(self, feed_source, feed_author, post):
        self.source = feed_source
        self.author = feed_author
        self.description = post.title
        self.direct_link = None
        # TODO: WTF DOES THIS DO? IT ONLY CREATES ONE LINK
        self.urls = [{'url': post.link,
                      'unshort_url': None,
                      'unique_id': build_hash(post.link),
                      'unshort_unique_id': None}]

        if 'published' in post:
            try:
                self.date_created = convert_date_rss_to_mysql(post.published)
            except AttributeError as e:
                self.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')
                logger.error(e)
                logger.error('Missing post.published from {} - {}'.format(post.feed['title'], post.title))
        elif 'updated' in post:
            try:
                self.date_created = convert_date_rss_to_mysql(post.updated)
            except AttributeError as e:
                self.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')
                logger.error(e)
                logger.error('Missing post.updated from {} - {}'.format(post.feed['title'], post.title))
        else:
            self.date_created = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d %H:%M:%S')
            logger.error('Missing published/updated info, setting published date to NOW.\n{} - {}'.format(post.feed['title'], post.title))

        self.unique_id_string = ','.join(
            sorted([url['unique_id'] for url in self.urls]))

        parsed_feed_entries_list.append(self)

    return parsed_feed_entries_list


class RedditPost(Post):
    def __init__(self, post):
        super().__init__()
        pass


class TwitterPost(Post):
    def __init__(self, post):
        super().__init__()
        pass
