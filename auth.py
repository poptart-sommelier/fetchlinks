import requests
import json
import logging

logger = logging.getLogger(__name__)


class Auth:
    def __init__(self, file: str = ''):
        self.file = file
        self.file_contents: dict = dict()
        if self.file != '':
            self.file_contents = self._read_secrets_file_json()

    def _read_secrets_file_json(self) -> dict:
        try:
            with open(self.file, 'r') as f:
                return json.load(f)
        except IOError as e:
            logger.error(e)


class RedditAuth(Auth):
    def __init__(self,
                 file: str = '',
                 app_client_id: str = '',
                 app_client_secret: str = ''):

        self.reddit_auth_api_url = 'https://www.reddit.com/api/v1/access_token'
        self.access_token = ''

        if file != '':
            super().__init__(file)
            self.set_secrets()
        elif app_client_id != '' and app_client_secret != '':
            self.app_client_id = app_client_id
            self.app_client_secret = app_client_secret
        else:
            super().__init__()

    def set_secrets(self):
        self.app_client_id = self.file_contents['reddit']['APP_CLIENT_ID']
        self.app_client_secret = self.file_contents['reddit']['APP_CLIENT_SECRET']

    def get_auth(self):
        headers = {'User-agent': 'fetch_links'}
        data = {'grant_type': 'client_credentials'}

        response = requests.post(self.reddit_auth_api_url,
                                 headers=headers,
                                 data=data,
                                 auth=(self.app_client_id, self.app_client_secret))

        response = response.json()
        self.access_token = response.get('access_token', '')

        if self.access_token != '':
            return self.access_token
        else:
            raise ValueError('Reddit authentication received an invalid access token')
