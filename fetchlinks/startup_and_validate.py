import argparse
import json
from pathlib import Path

import db_setup

VALID_FIELDS = {'db_info': ['db_name', 'db_location'],
                'log_info': ['log_config_location', 'log_location', 'log_level']}

SCRIPT_DIR = Path(__file__).resolve().parent


def _resolve_relative_to_script(path_str: str) -> Path:
    """Resolve a path string relative to the script directory if not absolute.

    Same convention as export_links.py so the app works regardless of cwd.
    """
    p = Path(path_str)
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    return p


def parse_sources(sources_location: str) -> dict:
    """
    Parse the sources config file.
    :param sources_location: location of the sources.json file
    :return: valid sources as dict
    """
    if Path(sources_location).exists():
        with open(sources_location, 'r', encoding='utf-8') as sources_file:
            sources = json.load(sources_file)
    else:
        raise FileNotFoundError('Sources file does not exist.')

    _validate_sources(sources)

    return sources


def _validate_sources(sources: dict):
    """
    Validates critical sources fields, mainly the creds field. Returns nothing, raises on error
    :param sources: parsed sources as dict
    :return: Nothing
    """
    # check if our api config files exists
    for source, settings in sources.items():
        if not isinstance(settings, dict):
            raise ValueError(f'{source} source settings must be a JSON object')

        if settings.get('enabled', True) is False:
            continue

        if settings.get('credential_location'):
            # Expand ~ so committed sources.json can use ~/.fetchlinks/...
            # instead of hardcoded absolute paths.
            expanded = str(Path(settings['credential_location']).expanduser())
            settings['credential_location'] = expanded
            if not Path(expanded).exists():
                raise FileNotFoundError(f'{source} credential file could not be found at location: {expanded}')

    rss_settings = sources.get('rss')
    if rss_settings and rss_settings.get('enabled', True):
        feeds = rss_settings.get('feeds')
        if not isinstance(feeds, list) or len(feeds) < 1:
            raise ValueError('The Rss config contains no feeds')

    if sources.get('bluesky') and sources['bluesky'].get('enabled', False):
        if not sources['bluesky'].get('credential_location'):
            raise ValueError('Bluesky source requires credential_location when enabled')

        timeline_limit = sources['bluesky'].get('timeline_limit', 50)
        if not isinstance(timeline_limit, int) or timeline_limit < 1:
            raise ValueError('Bluesky source timeline_limit must be a positive integer')


def parse_config(app_config_location: str) -> dict:
    """
    parses the config file
    :param app_config_location: location of the app_config
    :return: parsed config as dict
    """
    if Path(app_config_location).exists():
        with open(app_config_location, 'r', encoding='utf-8') as config_file:
            config = json.load(config_file)
        _validate_config(config)
        return config
    else:
        raise FileNotFoundError('Config file does not exist.')


def _validate_config(config: dict):
    """
    validates the config file fields
    :param config: config fields as dict
    :return: nothing
    """
    for header, fields in VALID_FIELDS.items():
        if header in config.keys():
            _validate_config_fields(config[header], fields)
        else:
            raise ValueError(f'Config file is missing config info: {header}.')

    # Anchor relative paths to the script directory so the app works
    # regardless of the current working directory.
    config['db_info']['db_location'] = str(
        _resolve_relative_to_script(config['db_info']['db_location'])
    )
    config['log_info']['log_location'] = str(
        _resolve_relative_to_script(config['log_info']['log_location'])
    )

    # Make sure the log directory exists.
    log_path = Path(config['log_info']['log_location'])
    log_path.parent.mkdir(parents=True, exist_ok=True)


def _validate_config_fields(config_keys, valid_keys):
    for field in valid_keys:
        if field not in config_keys:
            raise ValueError(f'Config file section has missing field: {field}')
        if not isinstance(config_keys[field], str):
            raise ValueError(f'Config file section has incorrect value in: {field}')


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-config', help='Config location', type=str, required=True)
    parser.add_argument('-sources', help='Sources location', type=str, required=True)
    return parser.parse_args()


def do_startup():
    # Parse arguments
    args = parse_arguments()

    # Config setup
    config = parse_config(args.config)

    # Config setup
    sources = parse_sources(args.sources)

    return config, sources
