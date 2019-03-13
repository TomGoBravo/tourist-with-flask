import pytest
from tourist import create_app
import shutil
import os.path
from tourist.config import config
from tourist.models import sqlalchemy


def path_relative(rel: str) -> str:
    """Converts path relative to this file to absolute path"""
    this_dir = os.path.dirname(__file__)
    return os.path.join(this_dir, rel)


@pytest.yield_fixture(scope='module')
def test_app():
    class TestConfig(config['dev']):
        SQLITE_DATABASE_PATH = '/tmp/touristtest.db'
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + SQLITE_DATABASE_PATH
        TESTING = True
        ALLOW_UNAUTHENTICATED_ADMIN = False

    shutil.copy(src=path_relative('spatial_metadata.sqlite'), dst=TestConfig.SQLITE_DATABASE_PATH)
    app = create_app(TestConfig)
    yield app
