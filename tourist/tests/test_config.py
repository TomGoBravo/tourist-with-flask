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


def test_development(monkeypatch):
    monkeypatch.setenv("TOURIST_ENV", "development")
    app = flask.Flask('testflask')
    app.config.from_object(tourist.config.by_env(flask_debug=True))

    assert 'www-data' not in app.config["SQLALCHEMY_DATABASE_URI"]
    assert 'www-data' not in app.config["LOG_DIR"]
    assert app.config["SECRET_KEY"] == '87294798798799'
    assert app.config["TESTING"]
