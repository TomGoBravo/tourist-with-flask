import csv
import enum
import io
from typing import List, Mapping

from sqlalchemy.util import IdentitySet

import attrs
import cattrs
import geojson
from geoalchemy2.shape import to_shape

from tourist.models import render
from tourist.models import tstore


@enum.unique
class RenderName(enum.Enum):
    PLACE_PREFIX = "/place/"
    PLACE_NAMES_WORLD = "/place_names_world"
    CSV_ALL = "/csv"
    POOLS_GEOJSON = "/pools.geojson"


def _build_render_club_source(orm_source: tstore.Source) -> render.ClubSource:
    return render.ClubSource(
        name=orm_source.name,
        logo_url=orm_source.logo_url,
        sync_timestamp=orm_source.sync_timestamp,
    )


def _build_render_club(orm_club: tstore.Club, source_by_short_name: Mapping[str, render.ClubSource]) -> render.Club:
    source = source_by_short_name.get(orm_club.source_short_name, None)
    return render.Club(
        id=orm_club.id,
        name=orm_club.name,
        short_name=orm_club.short_name,
        markdown=orm_club.markdown,
        status_date=orm_club.status_date,
        logo_url=orm_club.logo_url,
        source=source,
    )


def _build_render_pool(orm_pool: tstore.Pool) -> render.Pool:
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


def _build_render_place(orm_place: tstore.Place, source_by_short_name: Mapping[str, render.ClubSource]) -> render.Place:
    children_geojson = orm_place.children_geojson_features
    if children_geojson:
        geojson_children_collection = geojson.FeatureCollection(children_geojson)
    else:
        geojson_children_collection = {}

    child_clubs = [_build_render_club(c, source_by_short_name) for c in orm_place.child_clubs]
    child_pools = [_build_render_pool(p) for p in orm_place.child_pools]
    child_places = [render.ChildPlace(p.path, p.name) for p in orm_place.child_places]
    comments = [render.PlaceComment(id=c.id, timestamp=c.timestamp, content=c.content,
                                    content_markdown=c.content_markdown,
                                    source=c.source) for c in orm_place.comments]

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

    if orm_place.short_name == 'world':
        club: tstore.Club
        recently_updated = []
        for club in tstore.db.session.query(tstore.Club).filter(tstore.Club.status_date.like('____-__-__')).order_by(tstore.Club.status_date.desc()).limit(5):
            recently_updated.append(render.RecentlyUpdated(
                timestamp=club.status_datetime, path=club.path, club_name=club.name, place_name=club.parent.name))
        source: tstore.Source
        for source in tstore.db.session.query(tstore.Source).all():
            if not source.place_id:
                continue
            place: tstore.Place = tstore.db.session.get(tstore.Place, source.place_id)
            if not place:
                continue
            recently_updated.append(render.RecentlyUpdated(
                timestamp=source.sync_timestamp, path=place.path, place_name=place.name, source_name=source.name))
        recently_updated.sort(key=lambda ru: ru.timestamp, reverse=True)
    else:
        recently_updated = None

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
        recently_updated=recently_updated,
        comments=comments,
    )


def _build_place_recursive_names(orm_place: tstore.Place) \
        -> render.PlaceRecursiveNames:
    child_places = [_build_place_recursive_names(p) for p in orm_place.child_places]
    child_clubs = [render.PlaceRecursiveNames.Club(c.name) for c in orm_place.child_clubs]
    pools_with_links = list(p for p in orm_place.child_pools if bool(p.club_back_links))
    pools_without_links = list(p for p in orm_place.child_pools if not bool(p.club_back_links))
    child_pools = [render.PlaceRecursiveNames.Pool(p.name) for p in pools_with_links]
    child_pools_without_club_back_links = [render.PlaceRecursiveNames.Pool(p.name) for p in
                                           pools_without_links]
    return render.PlaceRecursiveNames(
        id=orm_place.id,
        name=orm_place.name,
        path=orm_place.path,
        area=orm_place.area,
        child_clubs=child_clubs,
        child_pools=child_pools,
        child_pools_without_club_back_links=child_pools_without_club_back_links,
        child_places=child_places,
        comment_count=len(orm_place.comments),
    )


def _build_geojson_feature_collection(all_places, all_pools):
    geojson_features = [p.entrance_geojson_feature for p in all_pools if p.entrance_geojson_feature]
    for p in all_places:
        if p.child_places or p.child_pools:
            continue
        geojson_features.append(p.center_geojson_feature)
    geojson_feature_collection = geojson.FeatureCollection(geojson_features)
    return geojson_feature_collection


def yield_cache():
    def get_all(cls):
        all_objects = IdentitySet(cls.query.all()) | tstore.db.session.dirty | tstore.db.session.new
        all_objects -= tstore.db.session.deleted
        return list(filter(lambda obj: isinstance(obj, cls), all_objects))

    all_places: List[tstore.Place] = get_all(tstore.Place)
    all_clubs: List[tstore.Club] = get_all(tstore.Club)
    all_pools: List[tstore.Pool] = get_all(tstore.Pool)
    all_sources: List[tstore.Source] = get_all(tstore.Source)
    source_by_short_name = {s.source_short_name: _build_render_club_source(s) for s in all_sources}

    for place in all_places:
        render_place = _build_render_place(place, source_by_short_name)
        yield tstore.RenderCache(name=RenderName.PLACE_PREFIX.value + place.short_name,
                                     value_dict=cattrs.unstructure(render_place))
        if place.short_name == 'world':
            render_names_world = _build_place_recursive_names(place)
            yield tstore.RenderCache(name=RenderName.PLACE_NAMES_WORLD.value,
                                         value_dict=attrs.asdict(
                                             render_names_world))

    geojson_feature_collection = _build_geojson_feature_collection(all_places, all_pools)

    yield tstore.RenderCache(name=RenderName.POOLS_GEOJSON.value,
                             value_str=geojson.dumps(geojson_feature_collection))

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
    yield tstore.RenderCache(name=RenderName.CSV_ALL.value, value_str=si.getvalue())


def get_place(short_name: str) -> render.Place:
    place_dict = tstore.RenderCache.query.get_or_404(
        RenderName.PLACE_PREFIX.value + short_name).value_dict
    return cattrs.structure(place_dict, render.Place)


def get_place_names_world() -> render.PlaceRecursiveNames:
    names_dict = tstore.RenderCache.query.get(RenderName.PLACE_NAMES_WORLD.value).value_dict
    return cattrs.structure(names_dict, render.PlaceRecursiveNames)


def get_string(name: RenderName) -> str:
    return tstore.RenderCache.query.get(name.value).value_str

