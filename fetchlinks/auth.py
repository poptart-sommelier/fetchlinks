import requests
import json
import logging

try:
    from atproto import Client as AtprotoClient
except ImportError:  # pragma: no cover - exercised in runtime when dependency missing
    AtprotoClient = None

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
            with open(file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except IOError as exc:
            raise RuntimeError(f'Unable to open secrets file: {file}') from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f'Secrets file is not valid JSON: {file}') from exc


class RedditAuth(Auth):
    """
    Reddit Authentication class.
    """
    def __init__(self, secrets_file: str = ''):
        super().__init__(secrets_file)

        self.app_client_secret: str = ''
        self.app_client_id: str = ''
        self.username: str = ''
        self.reddit_auth_api_url: str = 'https://www.reddit.com/api/v1/access_token'
        self.access_token: str = ''

        self.set_secrets()

    def set_secrets(self):
        try:
            self.app_client_id = self.file_contents['reddit']['APP_CLIENT_ID']
            self.app_client_secret = self.file_contents['reddit']['APP_CLIENT_SECRET']
            self.username = self.file_contents['reddit'].get('USERNAME', '')
        except KeyError as exc:
            raise ValueError('Missing required reddit credential keys in secrets file') from exc

    @property
    def user_agent(self) -> str:
        suffix = f' (by /u/{self.username})' if self.username else ''
        return f'linux:fetchlinks:0.1{suffix}'

    def get_auth(self):
        """
        Authenticate to Reddit's api endpoint and return an access token
        :return: a string representing reddit api access token
        """
        headers = {'User-Agent': self.user_agent}
        data = {'grant_type': 'client_credentials'}

        response = requests.post(self.reddit_auth_api_url,
                                 headers=headers,
                                 data=data,
                                 auth=(self.app_client_id, self.app_client_secret),
                                 timeout=20)

        response.raise_for_status()

        response = response.json()
        self.access_token = response.get('access_token', '')

        if self.access_token != '':
            return self.access_token
        else:
            raise ValueError('Reddit authentication received an invalid access token')


class BlueskyAuth(Auth):
    """
    Bluesky authentication class backed by the atproto SDK.
    """

    def __init__(self, secrets_file: str = ''):
        super().__init__(secrets_file)

        self.identifier: str = ''
        self.app_password: str = ''

        self.set_secrets()

    def set_secrets(self):
        try:
            self.identifier = self.file_contents['bluesky']['IDENTIFIER']
            self.app_password = self.file_contents['bluesky']['APP_PASSWORD']
        except KeyError as exc:
            raise ValueError('Missing required bluesky credential keys in secrets file') from exc

    def get_client(self):
        if AtprotoClient is None:
            raise RuntimeError('atproto is not installed. Install dependencies from requirements.txt first.')

        client = AtprotoClient()
        client.login(self.identifier, self.app_password)
        return client
