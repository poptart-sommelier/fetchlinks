import requests
from requests_oauthlib import OAuth1
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
    def __init__(self, secrets_file: str = ''):
        super().__init__(secrets_file)

        self.app_client_secret: str = ''
        self.app_client_id: str = ''
        self.reddit_auth_api_url: str = 'https://www.reddit.com/api/v1/access_token'
        self.access_token: str = ''

        self.set_secrets()

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


class TwitterAuth(Auth):
    def __init__(self, secrets_file: str = ''):
        super().__init__(secrets_file)

        self.consumer_key: str = ''
        self.consumer_secret: str = ''
        self.access_token: str = ''
        self.access_token_secret: str = ''

        self.set_secrets()

    def set_secrets(self):
        self.consumer_key = self.file_contents['twitter']['CONSUMER_KEY']
        self.consumer_secret = self.file_contents['twitter']['CONSUMER_SECRET']
        self.access_token = self.file_contents['twitter']['ACCESS_TOKEN']
        self.access_token_secret = self.file_contents['twitter']['ACCESS_TOKEN_SECRET']

    def get_auth(self):
        return OAuth1(self.consumer_key, self.consumer_secret,
                      self.access_token, self.access_token_secret)