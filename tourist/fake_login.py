import attr
import flask
from flask import flash, render_template, Blueprint
from flask_login import LoginManager, login_required, login_user, logout_user
from tourist.models import sqlalchemy


# Replace the github blueprint of the normal login.
fake_login_bp = Blueprint('github', __name__)


login_manager = LoginManager()
login_manager.login_view = 'github.login'
login_manager.anonymous_user = sqlalchemy.AnonymousUser


@login_manager.user_loader
def load_user(user_id):
    u = sqlalchemy.User.query.get(int(user_id))
    return u


@attr.s(auto_attribs=True)
class UserParams:
    """Parameters to create an sqlalchemy.User object."""
    username: str
    name: str
    edit_granted: bool


# A list of users that may be created by this fake_login module.
userparams = [
    UserParams('edituser', 'Pat Editor', True),
    UserParams('viewuser', 'Pat Viewer', False),
]


def get_user_by_username(username: str) -> sqlalchemy.User:
    assert username in {u.username for u in userparams}
    user = sqlalchemy.User.query.filter_by(username=username).one_or_none()
    if user is None:
        user = sqlalchemy.User(**attr.asdict(next(u for u in userparams if u.username == username)))
        sqlalchemy.db.session.add(user)
        sqlalchemy.db.session.commit()
    return user


@fake_login_bp.route('/login')
def login():
    username = flask.request.args.get('username')
    if username:
        user = get_user_by_username(username)
        login_user(user)
        flash("Successfully signed in with fake_login.")
        return flask.redirect('/')
    else:
        return render_template('fake_login.html', usernames=[u.username for u in userparams])


@fake_login_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have logged out")
    return flask.redirect("/")
