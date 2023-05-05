import tourist.config
import flask.config


def test_production(monkeypatch):
    monkeypatch.delenv("TOURIST_ENV", raising=False)
    app = flask.Flask('testflask')
    app.config.from_object(tourist.config.by_env(flask_debug=False))

    assert app.config["SQLALCHEMY_DATABASE_URI"] == 'sqlite:////data/tourist.db'
    assert app.config["LOG_DIR"] == '/data'
    assert app.config["SECRET_KEY"] == '87294798798799'
    assert not app.config["TESTING"]


def test_flask_debug_true_local(monkeypatch):
    monkeypatch.delenv("TOURIST_ENV", raising=False)
    app = flask.Flask('testflask')
    app.config.from_object(tourist.config.by_env(flask_debug=True))

    assert 'www-data' not in app.config["SQLALCHEMY_DATABASE_URI"]
    assert 'www-data' not in app.config["LOG_DIR"]
    assert app.config["SECRET_KEY"] == '87294798798799'
    assert app.config["TESTING"]
    assert app.config["SQLALCHEMY_DATABASE_URI"].endswith("/dev-data/tourist.db")


def test_workspace(monkeypatch):
    monkeypatch.setenv("TOURIST_ENV", "workspace")
    app = flask.Flask('testflask')
    app.config.from_object(tourist.config.by_env(flask_debug=True))

    assert app.config["SQLALCHEMY_DATABASE_URI"] == \
           "sqlite:////workspaces/tourist-with-flask/dev-data/tourist.db"
    assert app.config["LOG_DIR"]  == "/workspaces/tourist-with-flask/logs"
