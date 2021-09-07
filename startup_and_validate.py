import argparse
import json
from pathlib import Path

import db_setup

VALID_FIELDS = {'db_info': ['host', 'port', 'credential_location', 'db_name', 'db_location'],
                'log_info': ['log_config_location', 'log_location', 'log_level']}


def parse_sources(sources_location):
    if Path(sources_location).exists():
        with open(sources_location, 'r') as sources_file:
            sources = json.load(sources_file)
    else:
        raise FileNotFoundError('Sources file does not exist.')

    _validate_sources(sources)

    return sources


def _validate_sources(sources):
    # check if our api config files exists
    for source, settings in sources.items():
        if settings.get('credential_location', False):
            if not Path(settings.get('credential_location')).exists():
                raise FileNotFoundError(f'{source} credential file could not be found at location: {settings.get("credential_location")}')


def parse_config(app_config_location):
    if Path(app_config_location).exists():
        with open(app_config_location, 'r') as config_file:
            config = json.load(config_file)
        _validate_config(config)
        return config
    else:
        raise FileNotFoundError('Config file does not exist.')


def _validate_config(config):
    for header, fields in VALID_FIELDS.items():
        if header in config.keys():
            _validate_config_fields(config[header], fields)
        else:
            raise ValueError(f'Config file is missing config info: {header}.')

    # check if our log location exists
    if not Path(config['log_info']['log_location']).exists():
        Path(config['log_info']['log_location']).parent.mkdir(parents=True, exist_ok=True)

    # check if we already have a db
    if not Path(config['db_info']['db_location'] + config['db_info']['db_name']).exists():
        db_setup.db_initial_setup(config['db_info']['db_location'], config['db_info']['db_name'])


def _validate_config_fields(config_keys, valid_keys):
    for field in valid_keys:
        if config_keys.get(field, False):
            if not isinstance(config_keys.get(field), str):
                raise ValueError(f'Config file section has incorrect value in: {field}')
        else:
            raise ValueError(f'Config file section has missing field: {field}')


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-config', help='Config location', type=str)
    parser.add_argument('-sources', help='Sources location', type=str)
    return parser.parse_args()


def do_startup():
    # Parse arguments
    args = parse_arguments()

    # Config setup
    config = parse_config(args.config)

    # Config setup
    sources = parse_sources(args.sources)

    return config, sources
