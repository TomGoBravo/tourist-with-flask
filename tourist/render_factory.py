from typing import Iterable

import geojson
from geoalchemy2.shape import to_shape

from tourist.models import render
from tourist.models import sqlalchemy


def build_render_club(orm_club: sqlalchemy.Club) -> render.Club:
    return render.Club(
        id=orm_club.id,
        name=orm_club.name,
        short_name=orm_club.short_name,
        markdown=orm_club.markdown,
        status_date=orm_club.status_date,
    )


def build_render_pool(orm_pool: sqlalchemy.Pool) -> render.Pool:
    club_back_links = [render.ClubShortNameName(c.short_name, c.name) for c in orm_pool.club_back_links]

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
