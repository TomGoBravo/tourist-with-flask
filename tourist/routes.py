import flask
import geojson
from flask import render_template, Blueprint, redirect, url_for
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


@tourist_bp.route("/page/<string:short_name>")
def page_short_name(short_name):
    pool = sqlalchemy.Pool.query.filter_by(short_name=short_name).first()
    if pool:
        return redirect(pool.path)
    club = sqlalchemy.Club.query.filter_by(short_name=short_name).first()
    if club:
        return redirect(club.path)
    place = sqlalchemy.Place.query.filter_by(short_name=short_name).one()
    return redirect(place.path)


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
