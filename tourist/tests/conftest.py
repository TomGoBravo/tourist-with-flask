import pytest
from flask_login import FlaskLoginClient
from tourist import create_app
import shutil
import os.path
import tourist.config
import tourist.models.tstore

from contextlib import contextmanager


# From https://stackoverflow.com/a/51452451/341400
@contextmanager
def no_expire_on_commit():
    s = tourist.models.tstore.db.session()
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
    config = tourist.config.make_test_config(tmp_path)

    shutil.copy(src=path_relative('spatial_metadata.sqlite'), dst=config.SQLITE_DB_PATH)
    app = create_app(config)
    # FlaskLoginClient adds support for test_client(user=user)
    app.test_client_class = FlaskLoginClient
    yield app
