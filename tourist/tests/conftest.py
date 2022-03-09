import flask_wtf
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


# https://gist.github.com/singingwolfboy/2fca1de64950d5dfed72
import flask
from flask_wtf.csrf import generate_csrf
# Flask's assumptions about an incoming request don't quite match up with
# what the test client provides in terms of manipulating cookies, and the
# CSRF system depends on cookies working correctly. This little class is a
# fake request that forwards along requests to the test client for setting
# cookies.
class RequestShim(object):
    """
    A fake request that proxies cookie-related methods to a Flask test client.
    """
    def __init__(self, client):
        self.client = client
        self.vary = set({})

    def set_cookie(self, key, value='', *args, **kwargs):
        "Set the cookie on the Flask test client."
        server_name = flask.current_app.config["SERVER_NAME"] or "localhost"
        return self.client.set_cookie(
            server_name, key=key, value=value, *args, **kwargs
        )

    def delete_cookie(self, key, *args, **kwargs):
        "Delete the cookie on the Flask test client."
        server_name = flask.current_app.config["SERVER_NAME"] or "localhost"
        return self.client.delete_cookie(
            server_name, key=key, *args, **kwargs
        )


# We're going to extend Flask's built-in test client class, so that it knows
# how to look up CSRF tokens for you!
class FlaskClient(FlaskLoginClient):
    @property
    def csrf_token(self):
        # First, we'll wrap our request shim around the test client, so that
        # it will work correctly when Flask asks it to set a cookie.
        request = RequestShim(self)
        # Next, we need to look up any cookies that might already exist on
        # this test client, such as the secure cookie that powers `flask.session`,
        # and make a test request context that has those cookies in it.
        environ_overrides = {}
        self.cookie_jar.inject_wsgi(environ_overrides)
        with self.application.test_request_context(
                "/login", environ_overrides=environ_overrides,
            ):
            # Now, we call Flask-WTF's method of generating a CSRF token...
            csrf_token = generate_csrf()
            # ...which also sets a value in `flask.session`, so we need to
            # ask Flask to save that value to the cookie jar in the test
            # client. This is where we actually use that request shim we made!
            self.application.session_interface.save_session(flask.current_app, flask.session,
                                                             request)
            # And finally, return that CSRF token we got from Flask-WTF.
            return csrf_token


@pytest.fixture
def test_app(tmp_path):
    config = tourist.config.make_test_config(tmp_path)

    shutil.copy(src=path_relative('spatial_metadata.sqlite'), dst=config.SQLITE_DB_PATH)
    app = create_app(config)
    # FlaskLoginClient adds support for test_client(user=user)
    app.test_client_class = FlaskClient
    yield app
