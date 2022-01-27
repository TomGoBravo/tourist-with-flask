import pytest
from flask_login import FlaskLoginClient
from tourist import create_app
import shutil
import os.path
from tourist.config import config
import tourist.models.sqlalchemy

from contextlib import contextmanager


# From https://stackoverflow.com/a/51452451/341400
@contextmanager
def no_expire_on_commit():
    s = tourist.models.sqlalchemy.db.session()
    s.expire_on_commit = False
    try:
        yield
    finally:
        s.expire_on_commit = True


def path_relative(rel: str) -> str:
    """Converts path relative to this file to absolute path"""
    this_dir = os.path.dirname(__file__)
    return os.path.join(this_dir, rel)


@pytest.fixture
def test_app(tmp_path):
    class TestConfig(config['dev']):
        SQLITE_DATABASE_PATH = str(tmp_path / 'touristtest.db')
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + SQLITE_DATABASE_PATH
        TESTING = True
        ALLOW_UNAUTHENTICATED_ADMIN = False

    shutil.copy(src=path_relative('spatial_metadata.sqlite'), dst=TestConfig.SQLITE_DATABASE_PATH)
    app = create_app(TestConfig)
    # FlaskLoginClient adds support for test_client(user=user)
    app.test_client_class = FlaskLoginClient
    yield app
