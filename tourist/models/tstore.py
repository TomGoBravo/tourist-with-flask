"""
Objects that store hockey tourist data for editing.
"""

import datetime
import math
import re
from itertools import chain
from typing import Dict, Union, Optional
from typing import Iterable
from typing import List

import geojson
import attr
from flask_dance.consumer.storage.sqla import OAuthConsumerMixin
import flask_login
from geoalchemy2 import Geometry, WKTElement, WKBElement
from shapely.geometry.base import BaseGeometry
import sqlalchemy
from flask_sqlalchemy import SQLAlchemy
from shapely.geometry import mapping as shapely_mapping
import shapely
from geoalchemy2.shape import to_shape


# Copying https://stackoverflow.com/q/51065521/341400
from sqlalchemy_continuum import make_versioned
from sqlalchemy_continuum.plugins import FlaskPlugin

from tourist.models import attrib


db = SQLAlchemy()
# FLASK_APP=tourist flask db migrate
# failed with error
# (sqlite3.OperationalError) no such module: VirtualElementary [SQL: 'PRAGMA table_info("ElementaryGeometries")
#migrate = Migrate(db=db)

# make_versioned kills sync performance, from 2 seconds to 51 seconds for 900 items but having an
# online log of changes is nice.
make_versioned(plugins=[FlaskPlugin()])


@attr.s(auto_attribs=True)
class Bounds:
    north: float
    south: float
    west: float
    east: float


def optional_geometry_to_shape(geom: Optional[Union[str, WKTElement, WKBElement]]) -> BaseGeometry:
    if geom is None:
        return None
    elif isinstance(geom, (str,)):
        # During before_flush after a POST to /admin self.region is a str
        if geom.startswith('SRID=4326;'):
            geom = geom[10:]
        return shapely.wkt.loads(geom)
    else:
        return to_shape(geom)


SHORT_NAME_RE = r'[a-zA-Z][a-zA-Z0-9_-]+'

PAGE_LINK_RE = r'\[([^]]+)]\((?:/tourist)?/page/([^)]+)\)'

WIKI_LINK_RE = r'\[\[' + SHORT_NAME_RE + r'\]\]'

def _validate_short_name(short_name):
    if not re.fullmatch(SHORT_NAME_RE, short_name):
        raise ValueError(
            "short_name must start with a letter and be all letters (a-z), "
            f"numbers (0-9), underscore (_) and dash (-). Found '{short_name}'")


class Entity:
    """Base class for the main objects."""
    # TODO(TomGoBravo): Work out how to incorporate common methods (as_attrib_entity, ...) here.
    pass


class EntityChild:
    """Base class for children of Entity objects. Updating these triggers a render cache flush."""
    pass


class Place(db.Model, Entity):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    short_name = db.Column(db.String, nullable=False, unique=True)
    region = db.Column(Geometry(geometry_type='POLYGON', management=True, srid=4326))
    markdown = db.Column(db.String)
    parent_id = db.Column(db.Integer, db.ForeignKey('place.id'))
    parent = db.relationship('Place', remote_side=[id], backref='child_places')

    status_comment = db.Column(db.String, nullable=True)
    status_date = db.Column(db.String, nullable=True)
    geonames_id = db.Column(db.Integer, nullable=True)

    comments = db.relationship(
        "PlaceComment",
        back_populates='place',
        cascade="all, delete-orphan")

    __versioned__ = {}

    def __str__(self):
        if self.parent:
            parent_name = f' (in {self.parent.name})'
        else:
            parent_name = ''
        return f'{self.name}{parent_name}'

    def validate(self):
        _validate_short_name(self.short_name)
        if self.parent_id is None and self.parent is None and self.short_name != 'world':
            raise ValueError("parent unset. Every place must have a parent.")
        if self.markdown and re.search(WIKI_LINK_RE, self.markdown) is not None:
            raise ValueError("Place markdown must not contain [[Wiki Links]]")

    @property
    def parents(self):
        p = self.parent
        while p is not None:
            yield p
            p = p.parent

    @property
    def bounds(self):
        if self.region is None:
            return None
        polygon = to_shape(self.region)
        (minx, miny, maxx, maxy) = polygon.bounds
        return Bounds(
            north=maxy,
            south=miny,
            west=minx,
            east=maxx
        )

    @property
    def area(self) -> float:
        if self.region is None:
            return 0
        polygon = to_shape(self.region)
        # Area is in degrees^2, fairly meaningless beyond ranking places
        return polygon.area

    @property
    def path(self):
        return f'/tourist/place/{self.short_name}'

    @property
    def center_geojson_feature(self) -> Dict:
        if self.region is None:
            return {}
        else:
            polygon = to_shape(self.region)
            return {
                'type': 'Feature',
                'properties': {'title': self.name, 'path': self.path},
                'geometry': shapely_mapping(polygon.centroid),
            }

    @property
    def _pool_geojson_features(self) -> List[Dict]:
        return [p.entrance_geojson_feature for p in self.child_pools if p.entrance_geojson_feature]

    @property
    def children_or_center_geojson_features(self) -> List[Dict]:
        """Child places and pools of `self` or the center of the region. Use this to make sure
        `self` appears in some way on a map."""
        return self.children_geojson_features or [self.center_geojson_feature]

    @property
    def children_geojson_features(self) -> List[Dict]:
        """Child places and pools of `self` or an empty list if neither have geometry. Use this
        when showing a map that is already zoomed to the region of `self`."""
        pool_features = self._pool_geojson_features
        if pool_features or self.child_places:
            return pool_features + list(chain.from_iterable(c.children_or_center_geojson_features
                                                            for c in self.child_places))
        else:
            return []

    def as_attrib_entity(self):
        parent_short_name = self.parent and self.parent.short_name or ''
        return place_as_attrib_entity(self, parent_short_name)


def place_as_attrib_entity(place, parent_short_name: str):
    return attrib.Entity(
        type='place',
        id=place.id,
        name=place.name,
        short_name=place.short_name,
        markdown=place.markdown,
        parent_short_name=parent_short_name,
        region=optional_geometry_to_shape(place.region),
        geonames_id=place.geonames_id,
        status_comment=place.status_comment,
        status_date=place.status_date or None,
    )


class User(db.Model, flask_login.UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    # Your User model can include whatever columns you want: Flask-Dance doesn't care.
    # Here are a few columns you might find useful, but feel free to modify them
    # as your application needs!
    username = db.Column(db.String(256), unique=True)
    email = db.Column(db.String(256), unique=True)
    name = db.Column(db.String(256))
    edit_granted = db.Column(db.Boolean, default=False)

    @property
    def can_view_comments(self) -> bool:
        return self.edit_granted


# Anonymous user with same attributes as a logged in `User` for consistency in templates.
class AnonymousUser(flask_login.AnonymousUserMixin):
    edit_granted = False
    can_view_comments = False


class OAuth(OAuthConsumerMixin, db.Model):
    provider_user_id = db.Column(db.String(256), unique=True)
    provider_user_login = db.Column(db.String(256), unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey(User.id))
    user = db.relationship(User, backref='oauth')


# This many-many table is not maintained right now because clubs and their pools are expected to
# have the same parent place.
club_pools = db.Table('club_pools',
    db.Column('club_id', db.Integer, db.ForeignKey('club.id'), primary_key=True),
    db.Column('pool_id', db.Integer, db.ForeignKey('pool.id'), primary_key=True)
)


class Club(db.Model, Entity):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    short_name = db.Column(db.String, nullable=False, unique=True)
    markdown = db.Column(db.String)
    parent_id = db.Column(db.Integer, db.ForeignKey(Place.id))
    parent = db.relationship(Place, backref='child_clubs')
    pools = db.relationship('Pool', secondary=club_pools, lazy='subquery',
        backref=db.backref('clubs', lazy=True))
    status_comment = db.Column(db.String, nullable=True)
    # status_date is not set for clubs with source_short_name because we don't know when it was
    # last checked or verified.
    status_date = db.Column(db.String, nullable=True)
    source_short_name = db.Column(db.String, nullable=True)
    source_key = db.Column(db.String, nullable=True)
    logo_url = db.Column(db.String, nullable=True)

    __versioned__ = {}

    def __str__(self):
        if self.parent:
            parent_name = f' (in {self.parent.name})'
        else:
            parent_name = ''
        return f'{self.name}{parent_name}'

    def validate(self):
        _validate_short_name(self.short_name)
        # When a validating new instances that weren't read from the database the parent may
        # not yet have an id. This happens when it was created with `Club(parent=place)`.
        if self.parent_id is None and self.parent is None:
            raise ValueError("parent unset. Every Club must have a parent Place.")
        # TODO(TomGoBravo): Require status_date be always set
        if self.status_date:
            try:
                d = datetime.datetime.fromisoformat(self.status_date)
            except ValueError:
                raise ValueError("status_date must be a date in ISO YYYY-MM-DD format")

    @property
    def status_datetime(self):
        # status_date is always YYYY-MM-DD so a datetime.date makes sense but there are already some places that
        # expect a datetime.datetime.
        return datetime.datetime.fromisoformat(self.status_date)

    @property
    def path(self) -> str:
        return self.parent.path + '#' + self.short_name

    def as_attrib_entity(self):
        return club_as_attrib_entity(self, self.parent.short_name)


def club_as_attrib_entity(club, parent_short_name: str):
    return attrib.Entity(
        type='club',
        id=club.id,
        name=club.name,
        short_name=club.short_name,
        markdown=club.markdown,
        parent_short_name=parent_short_name,
        status_comment=club.status_comment,
        status_date=club.status_date or None,
    )


class Pool(db.Model, Entity):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    short_name = db.Column(db.String, nullable=False, unique=True)
    entrance = db.Column(Geometry(geometry_type='POINT', management=True, srid=4326))
    markdown = db.Column(db.String)
    parent_id = db.Column(db.Integer, db.ForeignKey(Place.id))
    parent = db.relationship(Place, backref='child_pools')
    status_comment = db.Column(db.String, nullable=True)
    status_date = db.Column(db.String, nullable=True)

    __versioned__ = {}

    def validate(self):
        _validate_short_name(self.short_name)
        # When a validating new instances that weren't read from the database the parent may
        # not yet have an id. This happens when it was created with `Club(parent=place)`.
        if self.parent_id is None and self.parent is None:
            raise ValueError("parent unset. Every Pool must have a parent Place.")
        if self.markdown and re.search(WIKI_LINK_RE, self.markdown) is not None:
            raise ValueError("Pool markdown must not contain [[Wiki Links]]")

    @property
    def club_back_links(self) -> List[Club]:
        """Returns clubs that have the same parent as and a [[wiki-link]] to, this pool."""
        clubs = []
        link_text = f'[[{self.short_name}]]'
        for club in self.parent.child_clubs:
            if club.markdown and link_text in club.markdown:
                clubs.append(club)
        return clubs

    def __str__(self):
        if self.parent:
            parent_name = f' (in {self.parent.name})'
        else:
            parent_name = ''
        return f'{self.name}{parent_name}'

    @property
    def entrance_geojson_feature(self) -> Dict:
        if self.entrance is None:
            return {}
        else:
            return {
                'type': 'Feature',
                'properties': {'title': self.name, 'path': self.path},
                'geometry': shapely_mapping(to_shape(self.entrance)),
            }

    @property
    def entrance_shapely(self):
        return to_shape(self.entrance)

    @property
    def maps_point_query(self) -> str:
        if self.entrance is None:
            return 'Please add pool location'
        entrance_shapely = to_shape(self.entrance)
        return f'{entrance_shapely.y:.6f},{entrance_shapely.x:.6f}'

    @property
    def path(self) -> str:
        # TODO: It is a little confusing to have the page scroll down to the pool because
        # most of the information is in the club, so lets not do that.
        #return self.parent.path + '#' + self.short_name
        return self.parent.path

    def as_attrib_entity(self):
        return pool_as_attrib_entity(self, self.parent.short_name)


def pool_as_attrib_entity(pool, parent_short_name):
    return attrib.Entity(
        type='pool',
        id=pool.id,
        name=pool.name,
        short_name=pool.short_name,
        markdown=pool.markdown,
        parent_short_name=parent_short_name,
        point=optional_geometry_to_shape(pool.entrance),
        status_comment=pool.status_comment,
        status_date=pool.status_date or None,
    )


from sqlalchemy.types import TypeDecorator, VARCHAR
import json

class JSONEncodedDict(TypeDecorator):
    """Represents an immutable structure as a json-encoded string.
    Usage::
        JSONEncodedDict(255)
    From https://docs.sqlalchemy.org/en/14/core/custom_types.html#marshal-json-strings
    """
    impl = VARCHAR
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = json.dumps(value, indent=None, separators=(',', ':'))
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            value = json.loads(value)
        return value


class Source(db.Model, EntityChild):
    """A data source. Instances of in this table are closely related to `Source` constants in `scripts/scrape.py`."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String)
    logo_url = db.Column(db.String)
    place_id = db.Column(db.Integer)
    source_short_name = db.Column(db.String, nullable=False, unique=True)
    sync_timestamp = db.Column(db.DateTime(), nullable=True)


class PlaceComment(db.Model, EntityChild):
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime(), default=datetime.datetime.utcnow)
    content = db.Column(db.String, nullable=True, default=None)
    content_markdown = db.Column(db.String, nullable=True, default=None)
    place_id = db.Column(db.Integer, db.ForeignKey(Place.id))

    remote_addr = db.Column(db.String, nullable=True, default=None)
    user_agent = db.Column(db.String, nullable=True, default=None)
    # From python-akismet SpamStatus: 0 - Not spam, 1 - Unknown, 2 - ProbableSpam, 3 - DefiniteSpam
    akismet_spam_status = db.Column(db.Integer, nullable=True, default=None)

    place = db.relationship("Place", back_populates="comments")


class RenderCache(db.Model):
    """A key-value store that caches data derived from other tables and passed to HTML templates.

    The value_dict contains JSON representations of structures in models/render.py. It'd make sense to store this
    in its owne key-value store, separate from tstore but I haven't set that up.
    """
    name = db.Column(db.String, primary_key=True, nullable=False, unique=True,
                     sqlite_on_conflict_primary_key='REPLACE')
    value_str = db.Column(db.String)
    value_dict = db.Column(JSONEncodedDict)


sqlalchemy.orm.configure_mappers()