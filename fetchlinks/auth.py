import requests
from requests_oauthlib import OAuth1
import json
import logging

logger = logging.getLogger(__name__)


class Auth:
    """
    Base class for Authentication
    """
    def __init__(self, file: str = ''):
        self.file = file
        self.file_contents: dict = dict()
        if self.file != '':
            self.file_contents = self.read_secrets_file_json(file)

    @staticmethod
    def read_secrets_file_json(file) -> dict:
        """
        Takes a file location and loads it as json
        :param file: location of the secrets file needed to connect to service
        :return: a json.load result
        """
        try:
            with open(file, 'r') as f:
                return json.load(f)
        except IOError as e:
            logger.error(e)


class RedditAuth(Auth):
    """
    Reddit Authentication class.
    """
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
        """
        Authenticate to Reddit's api endpoint and return an access token
        :return: a string representing reddit api access token
        """
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
    """
    Twitter Authentication class.
    """
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
        """
        Authenticate to twitter's api using provided secrets
        :return: OAuth1 authenticated token
        """
        return OAuth1(self.consumer_key, self.consumer_secret,
                      self.access_token, self.access_token_secret)
