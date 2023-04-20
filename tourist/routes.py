import collections
import datetime
import re
from typing import List
from typing import Optional

import attr
import flask
import flask_login
import sqlalchemy_continuum
import wtforms.validators
from flask import render_template, Blueprint, redirect, url_for
from flask_admin.contrib.geoa.fields import GeoJSONField
from sqlalchemy_continuum import transaction_class
from wtforms.validators import DataRequired

import tourist
from tourist import render_factory
from tourist.models import tstore

tourist_bp = Blueprint('tourist_bp', __name__)


def mapbox_access_token():
    return flask.current_app.config['MAPBOX_ACCESS_TOKEN']


# Render routes
#
# These routes read data from render_factory and don't modify stored data.

@tourist_bp.route("/")
def home_view_func():
    render_world = render_factory.get_place('world')
    return render_template("home.html", world=render_world, mapbox_access_token=mapbox_access_token())


@tourist_bp.route("/place/<string:short_name>")
def place_short_name(short_name):
    if short_name == 'world':
        return redirect(url_for('.home_view_func'))
    render_place = render_factory.get_place(short_name)
    return render_template("place.html", place=render_place,
                           mapbox_access_token=mapbox_access_token())


@tourist_bp.route("/data/pools.geojson")
def data_all_geojson():
    return render_factory.get_string(render_factory.RenderName.POOLS_GEOJSON)


@tourist_bp.route("/csv")
def csv_dump():
    csv_str = render_factory.get_string(render_factory.RenderName.CSV_ALL)
    output = flask.make_response(csv_str)
    output.headers["Content-Disposition"] = "attachment; filename=export.csv"
    output.headers["Content-type"] = "text/csv"
    return output


@tourist_bp.route("/list")
def list_view_func():
    render_world = render_factory.get_place_names_world()
    return render_template("list.html", world=render_world)


# Static routes
#
# These routes don't load any data from dynamic storage, but some templates have conditional
# parts based on login state.

@tourist_bp.route("/map")
def map_view_func():
    return render_template("map.html", mapbox_access_token=mapbox_access_token())


@tourist_bp.route("/about")
def about_view_func():
    return render_template("about.html")


@tourist_bp.route("/images/<path:path>")
def old_images_file(path):
    return flask.send_from_directory('static/pucku/images', path)


# Edit routes
#
# These routes modify data in the tstore

from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, BooleanField


def false_as_none(x):
    # Thanks to https://stackoverflow.com/a/21853689/341400
    return x or None


class ClubForm(FlaskForm):
    name = StringField('name', validators=[DataRequired()])
    markdown = TextAreaField('markdown')
    status_date = StringField('status_date', filters=[false_as_none])
    status_comment = StringField('status_comment', filters=[false_as_none])


@tourist_bp.route("/edit/club/<int:club_id>", methods=['GET', 'POST'])
def edit_club(club_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    club = tstore.Club.query.get_or_404(club_id)
    form = ClubForm(obj=club)
    if form.validate_on_submit():
        form.populate_obj(club)
        form_delete_place_comments()
        flask.flash(f"Updated {club.name}")
        tstore.db.session.commit()
        return redirect(club.path)
    return render_template("edit_club.html", form=form, club=club)


class PlaceForm(FlaskForm):
    name = StringField('name', validators=[DataRequired()])
    markdown = TextAreaField('markdown')
    region = GeoJSONField('region', srid=4326, session=tstore.db.session, geometry_type="POLYGON")
    status_date = StringField('status_date', filters=[false_as_none])
    status_comment = StringField('status_comment', filters=[false_as_none])


@tourist_bp.route("/edit/place/<int:place_id>", methods=['GET', 'POST'])
def edit_place(place_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    place = tstore.Place.query.get_or_404(place_id)
    form = PlaceForm(obj=place)
    if form.validate_on_submit():
        form.populate_obj(place)
        form_delete_place_comments()
        flask.flash(f"Updated {place.name}")
        tstore.db.session.commit()
        return redirect(place.path)
    return render_template("edit_place.html", form=form, place=place)


def form_delete_place_comments():
    """Delete PlaceComment objects that are checked in the form"""
    for k, v in flask.request.form.items():
        m = re.fullmatch(r'delete_place_comment_(\d+)', k)
        if m:
            comment = tstore.PlaceComment.query.get(int(m.group(1)))
            tstore.db.session.delete(comment)


@tourist_bp.route("/add/place_comment/<int:place_id>", methods=['POST'])
def add_place_comment(place_id):
    place = tstore.Place.query.get_or_404(place_id)
    content = flask.request.form['content'].strip()
    if content:
        request = flask.request
        place_comment = tstore.PlaceComment(
            source=f"Web visitor at {request.remote_addr}",
            content=content,
            place=place,
            remote_addr=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        tstore.db.session.add(place_comment)
        tstore.db.session.commit()
        flask.flash(f"Comment added to {place.name}")
    else:
        flask.flash(f"Ignored empty comment for {place.name}")
    return redirect(place.path)


class DeleteItemForm(FlaskForm):
    confirm = BooleanField('confirm', default="false", validators=[
        wtforms.validators.DataRequired()])


def delete_pool_and_flash(pool: tstore.Pool):
    flask.flash(f"Deleting {pool.name}")
    tstore.db.session.delete(pool)


def delete_club_and_flash(club: tstore.Club):
    flask.flash(f"Deleting {club.name}")
    tstore.db.session.delete(club)


def delete_place_children_and_flash(place: tstore.Place):
    if place.child_places:
        raise ValueError("Attempt to delete place with child_places")
    for pool in place.child_pools:
        delete_pool_and_flash(pool)
    for club in place.child_clubs:
        delete_club_and_flash(club)
    flask.flash(f"Deleting {place.name}")
    tstore.db.session.delete(place)


@tourist_bp.route("/delete/place/<int:place_id>", methods=['GET', 'POST'])
def delete_place(place_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    place = tstore.Place.query.get_or_404(place_id)

    form = DeleteItemForm(data={'confirm': False})

    if form.validate_on_submit():
        parent_path = place.parent.path
        delete_place_children_and_flash(place)
        tstore.db.session.commit()
        return redirect(parent_path)

    return render_template('delete_place.html', form=form, place=place)


@tourist_bp.route("/delete/club/<int:club_id>", methods=['GET', 'POST'])
def delete_club(club_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    club = tstore.Club.query.get_or_404(club_id)

    form = DeleteItemForm(data={'confirm': False})

    if form.validate_on_submit():
        parent_path = club.parent.path
        delete_club_and_flash(club)
        tstore.db.session.commit()
        return redirect(parent_path)

    return render_template('delete_club.html', form=form, club=club)


@tourist_bp.route("/delete/pool/<int:pool_id>", methods=['GET', 'POST'])
def delete_pool(pool_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    pool = tstore.Pool.query.get_or_404(pool_id)

    form = DeleteItemForm(data={'confirm': False})

    if form.validate_on_submit():
        if pool.club_back_links:
            raise ValueError("Attempt to delete pool when it has club_back_links")
        parent_path = pool.parent.path
        delete_pool_and_flash(pool)
        tstore.db.session.commit()
        return redirect(parent_path)

    return render_template('delete_pool.html', form=form, pool=pool)


# Miscellaneous routes
#
# These routes don't fit in the above catagories

# Once upon a time some of the markdown included links such as /page/<club shortname> and
# /page/<pool shortname> and /page/<place shortname>. This function handles them, though it might
# be nice to retire the links and this code.
@tourist_bp.route("/page/<string:short_name>")
def page_short_name(short_name):
    pool = tstore.Pool.query.filter_by(short_name=short_name).one_or_none()
    if pool:
        return redirect(pool.path)
    club = tstore.Club.query.filter_by(short_name=short_name).one_or_none()
    if club:
        return redirect(club.path)
    place = tstore.Place.query.filter_by(short_name=short_name).one_or_none()
    if place:
        return redirect(place.path)
    flask.abort(404)


@tourist_bp.route("/<string:short_name>.html")
def old_place_html_file(short_name):
    place = tstore.Place.query.filter_by(short_name=short_name).one_or_none()
    if place is not None:
        return redirect(url_for('.place_short_name', short_name=place.short_name))
    # Default to trying to send a few old static files
    return flask.send_from_directory('static/pucku', f'{short_name}.html')


Transaction = transaction_class(tstore.Club)
ClubVersion = sqlalchemy_continuum.version_class(tstore.Club)
PlaceVersion = sqlalchemy_continuum.version_class(tstore.Place)
PoolVersion = sqlalchemy_continuum.version_class(tstore.Pool)


@attr.s(auto_attribs=True, slots=True)
class TransactionLog:
    issued_at: Optional[datetime.datetime]
    clubs: List[ClubVersion] = attr.ib(factory=list)
    pools: List[PoolVersion] = attr.ib(factory=list)
    places: List[PlaceVersion] = attr.ib(factory=list)


@tourist_bp.route("/transactionlog")
def log_view_func():
    manager = tstore.Club.__versioning_manager__
    tx_column = manager.option(tstore.Club, 'transaction_column_name')
    transaction_logs = collections.defaultdict(TransactionLog)
    for t in Transaction.query.all():
        transaction_logs[t.id] = TransactionLog(issued_at=t.issued_at)

    for club_version in ClubVersion.query.all():
        transaction_logs[getattr(club_version, tx_column)].clubs.append(club_version)

    for place_version in PlaceVersion.query.all():
        transaction_logs[getattr(place_version, tx_column)].places.append(place_version)

    for pool_version in PoolVersion.query.all():
        transaction_logs[getattr(pool_version, tx_column)].pools.append(pool_version)

    return render_template("transaction_log.html", transactions=transaction_logs.values())


@tourist_bp.route("/comments")
def comments_view_func():
    comments = list(tstore.PlaceComment.query.order_by(tstore.PlaceComment.timestamp).all())
    return render_template("comments.html", comments=comments)
