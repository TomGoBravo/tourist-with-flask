import flask
from flask_dance.consumer.storage.sqla import SQLAlchemyStorage
from flask_dance.contrib.github import make_github_blueprint
from flask_login import (
    LoginManager, current_user,
    login_required, login_user, logout_user)
from sqlalchemy.orm.exc import NoResultFound
from flask_dance.consumer import oauth_authorized, oauth_error
from .models.tstore import OAuth, db, AnonymousUser, User

github_blueprint = make_github_blueprint()
# url_prefix="/login" is set when registering this blueprint

# setup login manager
login_manager = LoginManager()
login_manager.login_view = 'github.login'
login_manager.anonymous_user = AnonymousUser


@login_manager.user_loader
def load_user(user_id):
    u = User.query.get(int(user_id))
    return u


# setup SQLAlchemy backend
github_blueprint.backend = SQLAlchemyStorage(OAuth, db.session, user=current_user)


# create/login local user on successful OAuth login
@oauth_authorized.connect_via(github_blueprint)
def github_logged_in(github_blueprint, token):
    if not token:
        flask.flash("Failed to log in with GitHub.", category="error")
        return False

    resp = github_blueprint.session.get("/user")
    if not resp.ok:
        msg = "Failed to fetch user info from GitHub."
        flask.flash(msg, category="error")
        return False

    github_info = resp.json()
    github_user_id = str(github_info["id"])
    github_login = str(github_info["login"])

    # Find this OAuth token in the database, or create it
    query = OAuth.query.filter_by(
        provider=github_blueprint.name,
        provider_user_id=github_user_id,
    )
    try:
        oauth = query.one()
    except NoResultFound:
        oauth = OAuth(
            provider=github_blueprint.name,
            provider_user_id=github_user_id,
            provider_user_login=github_login,
            token=token,
        )

    if oauth.user:
        login_user(oauth.user)
        flask.flash("Successfully signed in with GitHub.")

    else:
        # Create a new local user account for this user
        user = User(
            # Remember that `email` can be None, if the user declines
            # to publish their email address on GitHub!
            email=github_info["email"],
            name=github_info["name"],
        )
        # Associate the new local user account with the OAuth token
        oauth.user = user
        # Save and commit our database models
        db.session.add_all([user, oauth])
        db.session.commit()
        # Log in the new local user account
        login_user(user)
        flask.flash("Successfully signed in with GitHub.")

    # Disable Flask-Dance's default behavior for saving the OAuth token
    return False


# notify on OAuth provider error
@oauth_error.connect_via(github_blueprint)
def github_error(github_blueprint, error, error_description=None, error_uri=None):
    msg = (
        "OAuth error from {name}! "
        "error={error} description={description} uri={uri}"
    ).format(
        name=github_blueprint.name,
        error=error,
        description=error_description,
        uri=error_uri,
    )
    flask.flash(msg, category="error")


@github_blueprint.route("/logout")
@login_required
def logout():
    logout_user()
    flask.flash("You have logged out")
    return flask.redirect("/")
