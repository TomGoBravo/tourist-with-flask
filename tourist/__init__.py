import datetime
import os
import re
from logging.handlers import RotatingFileHandler
import logging
import os.path
from typing import Optional

import attrs
import akismet
import flask
import humanize
import jinja2
import markdown
import markdown.extensions.wikilinks
from flask import current_app
from werkzeug.middleware.profiler import ProfilerMiddleware

import tourist.models.tstore
from tourist import render_factory
from tourist.models import tstore
from sqlalchemy import event
import tourist.config
from flaskext.markdown import Markdown
from flask_login import current_user

from tourist.wikilinks import WikiLinkExtension

UPDATE_RENDER_AFTER_FLUSH = 'update_render_after_flush'


def page_not_found(e):
    return flask.render_template('404.html'), 404


def inaccessible_response():
    # redirect to login page if not logged in
    current_app.logger.info(f'inaccessible_callback user={current_user} '
                            f'anon={current_user.is_anonymous} '
                            f'authn={current_user.is_authenticated} '
                            f'url={flask.request.url} '
                            f'remote_addr={flask.request.remote_addr} '
                            f'user_agent={flask.request.user_agent}')
    if current_user.is_anonymous:
        return flask.redirect(flask.url_for('github.login', next=flask.request.url))
    else:
        flask.abort(403)


def humanize_date_str(date_str) -> str:
    d = datetime.datetime.fromisoformat(date_str)
    return humanize.naturaltime(d)


def create_app(config_object: Optional[tourist.config.BaseConfig] = None):
    """ Bootstrap function to initialise the Flask app and config """
    app = flask.Flask(__name__)

    if config_object is None:
        config_object = tourist.config.by_env(flask_debug=app.debug)
    app.config.from_object(config_object)

    if os.path.isfile('tourist/secrets.cfg'):
        print('Loading secrets.cfg')
        app.config.from_pyfile('secrets.cfg')
    app.config.from_envvar('TOURIST_CONFIG_FILE', silent=True)
    app.jinja_options["undefined"] = jinja2.Undefined
    app.jinja_env.filters['humanize_date_str'] = humanize_date_str

    initialise_logger(app)
    app.logger.info(f'{__name__} starting up :)')
    app.logger.info(f'DB: {app.config["SQLALCHEMY_DATABASE_URI"]}')

    flask_md = Markdown(app)
    flask_md.register_extension(WikiLinkExtension, {})

    #app.wsgi_app = ProfilerMiddleware(app.wsgi_app,
    #                                  profile_dir='/home/thecap/code/tourist-with-flask/logs/profile')

    from tourist.models.tstore import db
    db.init_app(app)

    #migrate.init_app(app)

    # Posted to https://stackoverflow.com/a/53285907/341400
    # Decorator only works when it can get the current app.
    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def load_spatialite(dbapi_conn, connection_record):
            # From https://geoalchemy-2.readthedocs.io/en/latest/spatialite_tutorial.html
            dbapi_conn.enable_load_extension(True)
            dbapi_conn.load_extension('/usr/lib/x86_64-linux-gnu/mod_spatialite.so')

    with app.app_context():
        conn = db.engine.connect()
        # InitSpatialMetaData is very slow and only needs to be run when the database is first created.
        # There is a copy of an sqlite db with only this run in tests.
        #from sqlalchemy.sql import select, func
        #conn.execute(select([func.InitSpatialMetaData()]))
        db.create_all()

    app.logger.debug('Initialising Blueprints')
    from .routes import tourist_bp
    app.register_blueprint(tourist_bp, url_prefix='/tourist')

    @app.route('/')
    @app.route('/index.html')
    def pucku_home():
        return app.send_static_file('pucku/index.html')

    @app.route('/uwht/')
    def uwht_redirect():
        country_code = flask.request.args.get('country', None)
        if country_code and re.fullmatch(r'[a-z]{2}', country_code):
            return flask.redirect(f'/tourist/place/{country_code}')
        else:
            return flask.redirect('/tourist/')

    if app.config.get('USE_FAKE_LOGIN'):
        from .fake_login import fake_login_bp as login_bp, login_manager
    else:
        from .login import github_blueprint as login_bp, login_manager
    app.register_blueprint(login_bp, url_prefix='/login')
    login_manager.init_app(app)

    app.register_error_handler(404, page_not_found)

    from .admin import admin_views, pagedown
    admin_views.init_app(app)
    pagedown.init_app(app)

    from .scripts.sync import sync_cli
    app.cli.add_command(sync_cli)
    from .scripts.usertool import usertool_cli
    app.cli.add_command(usertool_cli)
    from .scripts.batchtool import batchtool_cli
    app.cli.add_command(batchtool_cli)
    from .scripts.scrape import scrape_cli
    app.cli.add_command(scrape_cli)

    @event.listens_for(db.session, "before_flush")
    def before_flush(session, flush_context, instances):
        update_render_after_flush = False
        for instance in session.new | session.dirty:
            if not session.is_modified(instance):
                continue
            if isinstance(instance, (tstore.Entity, tstore.EntityChild)):
                update_render_after_flush = True
            if isinstance(instance, tstore.Entity):
                instance.validate()

        for instance in session.deleted:
            if isinstance(instance, (tstore.Entity, tstore.EntityChild)):
                update_render_after_flush = True

        if update_render_after_flush:
            session.info[UPDATE_RENDER_AFTER_FLUSH] = True

    @event.listens_for(db.session, "after_flush_postexec")
    def after_flush_postexec(session, flush_context):
        if session.info.get(UPDATE_RENDER_AFTER_FLUSH, False):
            # ORM model objects that haven't been loaded from the database are slightly different
            # from those populated from a form. In particular `region` is a str instead of
            # geometry type. Instead of changing the render_factory to handle both types force
            # objects used for the render to be refreshed from the database.
            # expire_all() breaks some login tests so expire only pool/place/club objects.
            for instance in session.identity_map.values():
                if isinstance(instance, tstore.Entity):
                    session.expire(instance)
            new_cache_ids = []
            for new_cache in render_factory.yield_cache():
                session.add(new_cache)
                new_cache_ids.append(new_cache.name)
            # Remove rows in RenderCache not in new_cache_ids. This should be removed places.
            session.query(tstore.RenderCache).filter(tstore.RenderCache.name.notin_(
                new_cache_ids)).delete()
            del session.info[UPDATE_RENDER_AFTER_FLUSH]

    return app


def initialise_logger(app):
    """ Read environment config then initialise a 100MB rotating log """
    log_dir = app.config['LOG_DIR']
    log_level = app.config['LOG_LEVEL']

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    file_handler = RotatingFileHandler(log_dir + '/tourist-flask.log', 'a', 100 * 1024 * 1024, 3)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))

    app.logger.addHandler(file_handler)
    app.logger.setLevel(log_level)


def get_comment_spam_status(comment: tstore.PlaceComment):
    client = akismet.Akismet(flask.current_app.config["AKISMET_API_KEY"], blog="https://pucku.org/tourist/")
    return client.check(comment.remote_addr, comment.user_agent, comment_content=comment.content,
                        comment_type='comment', comment_date=comment.timestamp)
