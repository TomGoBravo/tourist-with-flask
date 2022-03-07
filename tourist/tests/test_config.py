import tourist.config
import flask.config


def test_production(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    app = flask.Flask('testflask')
    app.config.from_object(tourist.config.by_env[app.env])

    assert app.config["SQLALCHEMY_DATABASE_URI"] == 'sqlite:////var/local/www-data/tourist.db'
    assert app.config["LOG_DIR"] == '/var/local/www-data'
    assert app.config["SECRET_KEY"] == '87294798798799'
    assert not app.config["TESTING"]
    assert not app.config["DEBUG"]


def test_development(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    app = flask.Flask('testflask')
    app.config.from_object(tourist.config.by_env[app.env])

    assert 'www-data' not in app.config["SQLALCHEMY_DATABASE_URI"]
    assert 'www-data' not in app.config["LOG_DIR"]
    assert app.config["SECRET_KEY"] == '87294798798799'
    assert app.config["TESTING"]
    assert app.config["DEBUG"]
