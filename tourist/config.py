import logging


mapbox_access_token = 'pk.eyJ1IjoidG9tZ29icmF2byIsImEiOiJjajZwZzVyZnYwdGZlMnFvMTZyaXR3bmU3In0.fM7v2OUbs3hsBgwgioVIaA'


class EnvironmentConfig:
    """ Base class for config that is shared between environments """
    LOG_DIR = 'logs'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = '87294798798799'  # Flask-Admin uses flash messages, which need a session
    MAPBOX_MAP_ID = "streets-v11"
    MAPBOX_ACCESS_TOKEN = mapbox_access_token
    DEFAULT_CENTER_LAT = 1  # Must be non-zero to get included
    DEFAULT_CENTER_LONG = 1  # Must be non-zero to get included
    LEAFLET_CONTROL_GEOCODER = 1  # Enables geocoder control in flask-admin interface


class ProdConfig(EnvironmentConfig):
    """ Production Environment Config """
    LOG_DIR = '/var/local/www-data'
    LOG_LEVEL = logging.INFO
    SQLALCHEMY_DATABASE_URI = 'sqlite:////var/local/www-data/tourist.db'


class DevConfig(EnvironmentConfig):
    """ Dev Environment Config """
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + __file__.replace('/config.py', '.db')
    LOG_LEVEL = logging.DEBUG
    EXPLAIN_TEMPLATE_LOADING = False  # This is pretty noisy when enabled.
    USE_FAKE_LOGIN = True


# Pattern from app-deploy/config.py
config = {
    'production': ProdConfig,
    'dev': DevConfig,
}
