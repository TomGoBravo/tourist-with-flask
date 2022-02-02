import flask
import flask_login
import geojson
import wtforms.validators
from flask import render_template, Blueprint, redirect, url_for
from flask_admin.contrib.geoa.fields import GeoJSONField
from wtforms import FormField
from wtforms.validators import DataRequired

import tourist
from .models import sqlalchemy

tourist_bp = Blueprint('tourist_bp', __name__)

def mapbox_access_token():
    return sqlalchemy.db.get_app().config['MAPBOX_ACCESS_TOKEN']


@tourist_bp.route("/")
def home():
    world = sqlalchemy.Place.query.filter_by(short_name='world').first()
    return render_template("home.html", world=world, mapbox_access_token=mapbox_access_token())


@tourist_bp.route("/map")
def map():
    return render_template("map.html", mapbox_access_token=mapbox_access_token())


@tourist_bp.route("/about")
def about():
    return render_template("about.html")


@tourist_bp.route("/place/<string:short_name>")
def place_short_name(short_name):
    if short_name == 'world':
        return redirect(url_for('.home'))
    place = sqlalchemy.Place.query.filter_by(short_name=short_name).one()
    return render_template("place.html", place=place, mapbox_access_token=mapbox_access_token())


from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, DateField, BooleanField, FieldList

class ClubForm(FlaskForm):
    name = StringField('name', validators=[DataRequired()])
    markdown = TextAreaField('markdown')
    status_date = StringField('status_date')
    status_comment = StringField('status_comment')


@tourist_bp.route("/edit/club/<int:club_id>", methods=['GET', 'POST'])
def edit_club(club_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    club = sqlalchemy.Club.query.get_or_404(club_id)
    form = ClubForm(obj=club)
    if form.validate_on_submit():
        form.populate_obj(club)
        flask.flash(f"Updated {club.name}")
        sqlalchemy.db.session.commit()
        return redirect(club.path)
    return render_template("edit_club.html", form=form, club=club)


class PlaceForm(FlaskForm):
    name = StringField('name', validators=[DataRequired()])
    markdown = TextAreaField('markdown')
    region = GeoJSONField('region', srid=4326, session=sqlalchemy.db.session, geometry_type="POLYGON")
    status_date = StringField('status_date')
    status_comment = StringField('status_comment')


@tourist_bp.route("/edit/place/<int:place_id>", methods=['GET', 'POST'])
def edit_place(place_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    place = sqlalchemy.Place.query.get_or_404(place_id)
    form = PlaceForm(obj=place)
    if form.validate_on_submit():
        form.populate_obj(place)
        flask.flash(f"Updated {place.name}")
        sqlalchemy.db.session.commit()
        return redirect(place.path)
    return render_template("edit_place.html", form=form, place=place)


class DeleteItemForm(FlaskForm):
    confirm = BooleanField('confirm', default="false", validators=[
        wtforms.validators.DataRequired()])


def delete_pool_and_flash(pool: sqlalchemy.Pool):
    flask.flash(f"Deleting {pool.name}")
    sqlalchemy.db.session.delete(pool)


def delete_club_and_flash(club: sqlalchemy.Club):
    flask.flash(f"Deleting {club.name}")
    sqlalchemy.db.session.delete(club)


def delete_place_children_and_flash(place: sqlalchemy.Place):
    if place.child_places:
        raise ValueError("Attempt to delete place with child_places")
    for pool in place.child_pools:
        delete_pool_and_flash(pool)
    for club in place.child_clubs:
        delete_club_and_flash(club)
    flask.flash(f"Deleting {place.name}")
    sqlalchemy.db.session.delete(place)


@tourist_bp.route("/delete/place/<int:place_id>", methods=['GET', 'POST'])
def delete_place(place_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    place = sqlalchemy.Place.query.get_or_404(place_id)

    form = DeleteItemForm(data={'confirm': False})

    if form.validate_on_submit():
        parent_path = place.parent.path
        delete_place_children_and_flash(place)
        sqlalchemy.db.session.commit()
        return redirect(parent_path)

    return render_template('delete_place.html', form=form, place=place)


@tourist_bp.route("/delete/club/<int:club_id>", methods=['GET', 'POST'])
def delete_club(club_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    club = sqlalchemy.Club.query.get_or_404(club_id)

    form = DeleteItemForm(data={'confirm': False})

    if form.validate_on_submit():
        parent_path = club.parent.path
        delete_club_and_flash(club)
        sqlalchemy.db.session.commit()
        return redirect(parent_path)

    return render_template('delete_club.html', form=form, club=club)


@tourist_bp.route("/delete/pool/<int:pool_id>", methods=['GET', 'POST'])
def delete_pool(pool_id):
    if not (flask_login.current_user.is_authenticated and flask_login.current_user.edit_granted):
        return tourist.inaccessible_response()

    pool = sqlalchemy.Pool.query.get_or_404(pool_id)

    form = DeleteItemForm(data={'confirm': False})

    if form.validate_on_submit():
        if pool.club_back_links:
            raise ValueError("Attempt to delete pool when it has club_back_links")
        parent_path = pool.parent.path
        delete_pool_and_flash(pool)
        sqlalchemy.db.session.commit()
        return redirect(parent_path)

    return render_template('delete_pool.html', form=form, pool=pool)

@tourist_bp.route("/page/<string:short_name>")
def page_short_name(short_name):
    pool = sqlalchemy.Pool.query.filter_by(short_name=short_name).one_or_none()
    if pool:
        return redirect(pool.path)
    club = sqlalchemy.Club.query.filter_by(short_name=short_name).one_or_none()
    if club:
        return redirect(club.path)
    place = sqlalchemy.Place.query.filter_by(short_name=short_name).one_or_none()
    if place:
        return redirect(place.path)
    return flask.render_template('404.html'), 404


@tourist_bp.route("/<string:short_name>.html")
def old_place_html_file(short_name):
    place = sqlalchemy.Place.query.filter_by(short_name=short_name).one_or_none()
    if place is not None:
        return redirect(url_for('.place_short_name', short_name=place.short_name))
    # Default to trying to send a few old static files
    return flask.send_from_directory('static/pucku', f'{short_name}.html')


@tourist_bp.route("/images/<path:path>")
def old_images_file(path):
    return flask.send_from_directory('static/pucku/images', path)


@tourist_bp.route("/data/pools.geojson")
def data_all_geojson():
    children_geojson = [p.entrance_geojson_feature for p in sqlalchemy.Pool.query.all() if p.entrance_geojson_feature]
    return geojson.dumps(geojson.FeatureCollection(children_geojson))


@tourist_bp.route("/list")
def list():
    # This might not be very efficient but works.
    world = sqlalchemy.Place.query.filter_by(short_name='world').one()
    return render_template("list.html", world=world)
