import csv
import enum
import io
from typing import Iterable
from sqlalchemy.util import IdentitySet

import attrs
import cattrs
import geojson
from geoalchemy2.shape import to_shape

from tourist.models import render
from tourist.models import sqlalchemy


@enum.unique
class RenderName(enum.Enum):
    PLACE_PREFIX = "/place/"
    PLACE_NAMES_WORLD = "/place_names_world"
    CSV_ALL = "/csv"
    POOLS_GEOJSON = "/pools.geojson"


def build_render_club(orm_club: sqlalchemy.Club) -> render.Club:
    return render.Club(
        id=orm_club.id,
        name=orm_club.name,
        short_name=orm_club.short_name,
        markdown=orm_club.markdown,
        status_date=orm_club.status_date,
    )


def build_render_pool(orm_pool: sqlalchemy.Pool) -> render.Pool:
    club_back_links = [render.ClubShortNameName(short_name=c.short_name, name=c.name)
                       for c in orm_pool.club_back_links]

    return render.Pool(
        id=orm_pool.id,
        name=orm_pool.name,
        short_name=orm_pool.short_name,
        markdown=orm_pool.markdown,
        club_back_links=club_back_links,
        maps_point_query=orm_pool.maps_point_query,
    )


def build_render_place(orm_place: sqlalchemy.Place) -> render.Place:

    children_geojson = [p.entrance_geojson_feature for p in orm_place._descendant_pools if
                        p.entrance_geojson_feature]

    if children_geojson:
        geojson_children_collection = geojson.FeatureCollection(children_geojson)
    else:
        geojson_children_collection = {}

    child_clubs = [build_render_club(c) for c in orm_place.child_clubs]
    child_pools = [build_render_pool(p) for p in orm_place.child_pools]
    child_places = [render.ChildPlace(p.path, p.name) for p in orm_place.child_places]

    parents = []
    p = orm_place.parent
    while p:
        parents.append(render.ChildPlace(p.path, p.name))
        p = p.parent

    bounds = None
    if orm_place.bounds is not None:
        polygon = to_shape(orm_place.region)
        (minx, miny, maxx, maxy) = polygon.bounds
        bounds = render.Bounds(
            north=maxy,
            south=miny,
            west=minx,
            east=maxx
        )

    return render.Place(
        id=orm_place.id,
        name=orm_place.name,
        short_name=orm_place.short_name,
        markdown=orm_place.markdown,
        geojson_children_collection=geojson_children_collection,
        child_clubs=child_clubs,
        child_pools=child_pools,
        bounds=bounds,
        child_places=child_places,
        parents=parents,
    )


def build_place_recursive_names(orm_place: sqlalchemy.Place) -> render.PlaceRecursiveNames:
    child_places = [build_place_recursive_names(p) for p in orm_place.child_places]
    child_clubs = [render.PlaceRecursiveNames.Club(c.name) for c in orm_place.child_clubs]
    child_pools = [render.PlaceRecursiveNames.Pool(p.name) for p in orm_place.child_pools]
    return render.PlaceRecursiveNames(
        id=orm_place.id,
        name=orm_place.name,
        path=orm_place.path,
        area=orm_place.area,
        child_clubs=child_clubs,
        child_pools=child_pools,
        child_places=child_places,
    )


def yield_cache():
    def get_all(cls):
        all_objects = IdentitySet(cls.query.all()) | sqlalchemy.db.session.dirty | sqlalchemy.db.session.new
        all_objects -= sqlalchemy.db.session.deleted
        return list(filter(lambda obj: isinstance(obj, cls), all_objects))

    all_places = get_all(sqlalchemy.Place)
    all_clubs = get_all(sqlalchemy.Club)
    all_pools = get_all(sqlalchemy.Pool)

    for place in all_places:
        render_place = build_render_place(place)
        yield sqlalchemy.RenderCache(name=RenderName.PLACE_PREFIX.value + place.short_name,
                                     value_dict=attrs.asdict(render_place))
        if place.short_name == 'world':
            render_names_world = build_place_recursive_names(place)
            yield sqlalchemy.RenderCache(name=RenderName.PLACE_NAMES_WORLD.value,
                                         value_dict=attrs.asdict(
                                             render_names_world))

    children_geojson = [p.entrance_geojson_feature for p in all_pools if p.entrance_geojson_feature]
    yield sqlalchemy.RenderCache(name=RenderName.POOLS_GEOJSON.value,
                                 value_str=geojson.dumps(geojson.FeatureCollection(
                                     children_geojson)))

    si = io.StringIO()
    cw = csv.DictWriter(si, extrasaction='ignore',
                        fieldnames=['type', 'id', 'short_name', 'name', 'parent_short_name',
                                    'markdown', 'status_date', 'status_comment'])
    cw.writeheader()
    for place in all_places:
        cw.writerow(attrs.asdict(place.as_attrib_entity()))
    for club in all_clubs:
        if not club.parent:
            print("odd")
            pass
        cw.writerow(attrs.asdict(club.as_attrib_entity()))
    for pool in all_pools:
        cw.writerow(attrs.asdict(pool.as_attrib_entity()))
    yield sqlalchemy.RenderCache(name=RenderName.CSV_ALL.value, value_str=si.getvalue())


def get_place(short_name: str) -> render.Place:
    place_dict = sqlalchemy.RenderCache.query.get_or_404(
        RenderName.PLACE_PREFIX.value + short_name).value_dict
    return cattrs.structure(place_dict, render.Place)


def get_place_names_world() -> render.PlaceRecursiveNames:
    names_dict = sqlalchemy.RenderCache.query.get(RenderName.PLACE_NAMES_WORLD.value).value_dict
    return cattrs.structure(names_dict, render.PlaceRecursiveNames)


def get_string(name: RenderName) -> str:
    return sqlalchemy.RenderCache.query.get(name.value).value_str

