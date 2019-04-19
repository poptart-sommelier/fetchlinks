class Datastructure:
    def __init__(self):
        self.data_structure = {
            'source': '',
            'author': '',
            'description': '',
            'direct_link': '',
            'urls': [],
            'date_created': '',
            'unique_id_string': '',
            'url_1': '',
            'url_2': '',
            'url_3': '',
            'url_4': '',
            'url_5': '',
            'url_6': '',
            'urls_missing': 0
        }

    def prep_for_db(self):
        # break out urls to our url fields (max 6), warn if we have more than 6. Also truncates
        # urls that are longer than our VARCHAR(2200) limit in the DB.

        for i, url in enumerate(self.data_structure['urls']):
            if i > 5:
                self.data_structure['urls_missing'] = 1
                break

            if url['unshort_url'] is None:
                self.data_structure['url_' + str(i + 1)] = url['url']
            else:
                self.data_structure['url_' + str(i + 1)] = url['unshort_url']

            if len(self.data_structure['url_' + str(i + 1)]) > 2200:
                self.data_structure['url_' + str(i + 1)] = self.data_structure['url_1' + str(i + 1)][0:2180]\
                                                           + '[TRUNCATED]'
