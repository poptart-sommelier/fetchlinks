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


