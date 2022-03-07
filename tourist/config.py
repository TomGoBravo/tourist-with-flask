import logging
import os
import pathlib

mapbox_access_token = 'pk.eyJ1IjoidG9tZ29icmF2byIsImEiOiJjajZwZzVyZnYwdGZlMnFvMTZyaXR3bmU3In0.fM7v2OUbs3hsBgwgioVIaA'


class BaseConfig:
    """ Base class for config that is shared between environments """
    LOG_DIR = 'logs'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = '87294798798799'  # Flask-Admin uses flash messages, which need a session
    MAPBOX_MAP_ID = "streets-v11"
    MAPBOX_ACCESS_TOKEN = mapbox_access_token
    DEFAULT_CENTER_LAT = 1  # Must be non-zero to get included
    DEFAULT_CENTER_LONG = 1  # Must be non-zero to get included
    LEAFLET_CONTROL_GEOCODER = 1  # Enables geocoder control in flask-admin interface
    DATA_DIR: pathlib.Path

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return f'sqlite:///{str(self.DATA_DIR)}/tourist.db'


class ProductionConfig(BaseConfig):
    """ Production Environment Config """
    LOG_DIR = '/var/local/www-data'
    DATA_DIR = pathlib.Path('/var/local/www-data')
    LOG_LEVEL = logging.INFO


class DevelopmentConfig(BaseConfig):
    """ Dev Environment Config """
    LOG_LEVEL = logging.DEBUG
    EXPLAIN_TEMPLATE_LOADING = False  # This is pretty noisy when enabled.
    USE_FAKE_LOGIN = True
    TESTING = True

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        sqlite_db_path = os.getenv('SQLITE_DB_PATH', str(self.DATA_DIR / 'tourist.db'))
        return f'sqlite:///{sqlite_db_path}'

    @property
    def DATA_DIR(self) -> pathlib.Path:
        # This isn't tested because it is run when config.py is imported.
        path = pathlib.Path(__file__).parent.parent / "dev-data"
        if not path.exists():
            path.mkdir()
        return path


by_env = {
    'production': ProductionConfig(),
    'development': DevelopmentConfig(),
}
