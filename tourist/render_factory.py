import csv
import datetime
import enum
import io
import itertools
import logging
from collections import defaultdict
from typing import List, Mapping
from typing import Type
from typing import Union

from more_itertools import one
from shapely.geometry import mapping as shapely_mapping

from sqlalchemy.util import IdentitySet

import attrs
import cattrs
import geojson
from geoalchemy2.shape import to_shape

from tourist import continuumutils
from tourist.models import render
from tourist.models import tstore


@enum.unique
class RenderName(enum.Enum):
    PLACE_PREFIX = "/place/"
    PLACE_NAMES_WORLD = "/place_names_world"
    CSV_ALL = "/csv"
    POOLS_GEOJSON = "/pools.geojson"
    BE_GEOJSON = "/be.geojson"
    PROBLEMS = "/problems_list"


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


def _build_changes(orm_entity: Union[tstore.Place, tstore.Club, tstore.Pool], versions:
        continuumutils.VersionTables) -> (render.PlaceEntityChanges):
    changes = render.PlaceEntityChanges(entity_name=orm_entity.name)

    prev_v = None
    for v in versions.get_object_history(orm_entity):
        issued_at = versions.transaction_issued_at[v.transaction_id]
        user_email = versions.transaction_user_email.get(v.transaction_id, None)
        changes.changes.append(render.PlaceEntityChanges.Change(
            timestamp=issued_at, user=user_email,
            change=str(continuumutils.changeset(v, prev_v))))
        prev_v = v
    return changes


def _build_render_place(orm_place: tstore.Place, source_by_short_name: Mapping[str,
      render.ClubSource], versions: continuumutils.VersionTables) -> (render.Place):
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

    if orm_place.is_world:
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
        entity_changes = None
    else:
        recently_updated = None
        entity_changes = [_build_changes(orm_place, versions)]
        for child in itertools.chain(orm_place.child_places, orm_place.child_pools,
                                           orm_place.child_clubs):
            entity_changes.append(_build_changes(child, versions))


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
        changes=entity_changes,
    )


def _build_place_recursive_names(orm_place: tstore.Place) \
        -> render.PlaceRecursiveNames:
    child_places = [_build_place_recursive_names(p) for p in orm_place.child_places]
    child_clubs = [render.PlaceRecursiveNames.Club(c.name) for c in orm_place.child_clubs]
    pool_by_has_links = defaultdict(list)
    for p in orm_place.child_pools:
        pool_by_has_links[bool(p.club_back_links)].append(p)
    pools_with_links = pool_by_has_links[True]
    pools_without_links = pool_by_has_links[False]
    child_pools_with_club_back_links = [render.PlaceRecursiveNames.Pool(p.name) for p in
                                        pools_with_links]
    child_pools_without_club_back_links = [render.PlaceRecursiveNames.Pool(p.name) for p in
                                           pools_without_links]
    return render.PlaceRecursiveNames(
        id=orm_place.id,
        name=orm_place.name,
        path=orm_place.path,
        area=orm_place.area,
        child_clubs=child_clubs,
        child_pools=child_pools_with_club_back_links,
        child_pools_without_club_back_links=child_pools_without_club_back_links,
        child_places=child_places,
        comment_count=len(orm_place.comments),
    )


def _build_geojson_feature_collection(all_places, all_pools):
    pools_for_geojson = [p for p in all_pools if p.has_entrance_and_club_back_links]
    geojson_features = [p.entrance_geojson_feature for p in pools_for_geojson]
    set_pools_for_geojson = set(pools_for_geojson)
    for p in all_places:
        if p.child_places or len(set(p.child_pools).intersection(set_pools_for_geojson)):
            continue
        geojson_features.append(p.center_geojson_feature)
    geojson_feature_collection = geojson.FeatureCollection(geojson_features)
    return geojson_feature_collection


def _build_be_geojson_feature_collection(be_place: tstore.Place):
    """Returns a GeoJSON FeatureCollection especially for belgiumuwh.be"""
    geojson_features = []
    for town in be_place.child_places:
        polygon = to_shape(town.region)
        clubs = list(town.child_clubs)
        if len(clubs) == 1:
            club = one(clubs)
            geojson_features.append({
                'type': 'Feature',
                'properties': {'title': club.name},
                'geometry': shapely_mapping(polygon.centroid),
            })
        else:
            # TODO(TomGoBravo): Log this to something like sentry so it isn't buried in a log file.
            logging.warning(f"Town {town.name} does not have one club")
    geojson_feature_collection = geojson.FeatureCollection(geojson_features)
    return geojson_feature_collection


@attrs.frozen(order=True)
class StatusDateClub:
    status_date: datetime.date = attrs.field(order=True)
    club: tstore.Club = attrs.field(order=False)


def _build_problems(all_places: List[tstore.Place], all_clubs: List[tstore.Club]) -> List[
    render.Problem]:
    """Returns a list of data quality problems found in the places and clubs."""
    problems = []
    for place in all_places:
        if place.area == 0 and not place.is_world:
            problems.append(render.Problem(place.path,
                                           place.name,
                                           "Add place location as a polygon on the map"))
    status_date_clubs = []
    for club in all_clubs:
        if club.source_short_name:
            continue
        try:
            parsed = datetime.date.fromisoformat(club.status_date)
        except (ValueError, TypeError):
            parsed = None
        if parsed:
            status_date_clubs.append(StatusDateClub(parsed, club))
        else:
            problems.append(
                render.Problem(club.path,
                               club.parent.name,
                               f"Add a status_date as a valid YYYY-MM-DD to {club.name}"))
    status_date_clubs.sort()
    for sdc in status_date_clubs[0:5]:
        problems.append(render.Problem(
            sdc.club.path,
            sdc.club.parent.name,
            f"Track down what's happening with {sdc.club.name}, status_date is {sdc.status_date}"))
    return problems


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
    version_tables = continuumutils.VersionTables.make()
    version_tables.populate()

    for place in all_places:
        render_place = _build_render_place(place, source_by_short_name, version_tables)
        yield tstore.RenderCache(name=RenderName.PLACE_PREFIX.value + place.short_name,
                                     value_dict=cattrs.unstructure(render_place))
        if place.is_world:
            render_names_world = _build_place_recursive_names(place)
            yield tstore.RenderCache(name=RenderName.PLACE_NAMES_WORLD.value,
                                         value_dict=attrs.asdict(
                                             render_names_world))

    yield tstore.RenderCache(name=RenderName.PROBLEMS.value,
                             value_dict=cattrs.unstructure(render.Problems(_build_problems(
                                 all_places, all_clubs))))

    geojson_feature_collection = _build_geojson_feature_collection(all_places, all_pools)

    yield tstore.RenderCache(name=RenderName.POOLS_GEOJSON.value,
                             value_str=geojson.dumps(geojson_feature_collection))

    be_place = tstore.Place.query.filter_by(short_name='be').first()
    if be_place:
        be_geojson_feature_collection = _build_be_geojson_feature_collection(be_place)
        yield tstore.RenderCache(name=RenderName.BE_GEOJSON.value,
                                 value_str=geojson.dumps(be_geojson_feature_collection))

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


def get_problems() -> render.Problems:
    problems_dict = tstore.RenderCache.query.get_or_404(RenderName.PROBLEMS.value ).value_dict
    return cattrs.structure(problems_dict, render.Problems)
