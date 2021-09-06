import argparse
import json
from pathlib import Path

import db_setup

VALID_DB_INFO_FIELDS = ['host', 'port', 'credential_location', 'db_name', 'db_location', 'db_full_path']
VALID_LOG_INFO_FIELDS = ['log_config_location', 'log_location', 'log_level']


def parse_config(app_config_location):
    if Path(app_config_location).exists():
        with open(app_config_location, 'r') as config_file:
            config = config_file.read()
        config = json.loads(config)
        _validate_config(config)
        return config
    else:
        raise FileNotFoundError('Config file does not exist.')


def _validate_config_fields(config_portion, valid_fields):
    for field in valid_fields:
        if config_portion.get(field, False):
            if not isinstance(config_portion.get(field), str):
                raise ValueError(f'Config file section has incorrect value in: {field}')
        else:
            raise ValueError(f'Config file section has missing field: {field}')


def _validate_config(config):
    if config.get('db_info', False):
        _validate_config_fields(config.get('db_info'), VALID_DB_INFO_FIELDS)
    else:
        raise ValueError('Config file is missing database config info.')

    if config.get('log_info', False):
        _validate_config_fields(config.get('log_info'), VALID_LOG_INFO_FIELDS)
    else:
        raise ValueError('Config file is missing log config info.')


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-config', help='Config location', type=str)

    return parser.parse_args()


def do_startup():
    # Parse arguments
    args = parse_arguments()

    # Config setup
    config = parse_config(args.config)

    # check if our log location exists
    if not Path(config['log_info']['log_location']).exists():
        Path(config['log_info']['log_location']).parent.mkdir(parents=True, exist_ok=True)

    # check if we already have a db
    if not Path(config['db_info']['db_full_path']).exists():
        db_setup.db_initial_setup(config['db_info']['db_location'], config['db_info']['db_name'])

    return config
