import collections
import itertools
import json
import logging
import warnings
from collections import defaultdict
from datetime import datetime
from datetime import date
import re
from typing import Any
from typing import Callable
from typing import Dict
from typing import ForwardRef
from typing import Iterable
from typing import List
from typing import Mapping
from typing import MutableMapping
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Type
from urllib.parse import urlparse

import attr
import bs4
import cattrs
import click
import flask
import geoalchemy2.shape
import geopy.point
import geopy.distance
import prefect
import shapely.geometry
import sqlalchemy.orm
import sqlalchemy.orm.exc

import requests
from flask.cli import AppGroup
from geoalchemy2.shape import from_shape
from markdownify import MarkdownConverter
from more_itertools import one
from sortedcontainers import SortedList

import tourist.render_factory
from tourist.models import sstore
import attrs
from pydantic.dataclasses import dataclass as pydantic_dataclass


from tourist.models import tstore
from tourist.scripts import sync

scrape_cli = AppGroup('scrape')


@pydantic_dataclass(frozen=True)
class Source:
    """Scraper source configuration"""
    short_name: str
    url: str
    place_short_name: str
    extractor: Callable[[tstore.Place, sstore.UrlFetch], List[sstore.EntityExtract]]


# latitude, longitude: the order expected by geopy.distance. This is opposite the order used by
# shapely and WKT!
# shapely and geopy points are mutable and not hashable so I'm defining a very simple type
# for locations. Altitude is NOT included because spatialite seems to silently drop WKB
# coordinates with Z; it took me a while to work this out.
@attrs.frozen(eq=True, order=True, slots=True)
class PointFrozen:
    latitude: float
    longitude: float

    @property
    def geopy_point(self):
        return geopy.point.Point(self.latitude, self.longitude)


def distance(pt1: PointFrozen, pt2: PointFrozen) -> geopy.distance.Distance:
    return geopy.distance.distance(pt1.geopy_point, pt2.geopy_point)


@attrs.frozen(eq=True, order=True)
class SourceContext:
    source_short_name: str
    club_name: str = ''
    session: Optional['GbUwhFeed.ClubSession'] = None
    parent_id: Optional[int] = None
    parent_short_name: Optional[str] = None
    tstore_pool: Optional[tstore.Pool] = None


@attrs.frozen(eq=True, order=True)
class PoolFrozen:
    """A pool location and name, used when syncing pools from tstore and feed sources."""
    latitude: float
    longitude: float
    name: str
    contexts: List[SourceContext] = attrs.field(eq=False, order=False, factory=list)

    @property
    def source_short_name(self) -> List[str]:
        return sorted(set(c.source_short_name for c in self.contexts))

    @property
    def point_frozen(self) -> PointFrozen:
        return PointFrozen(latitude=self.latitude, longitude=self.longitude)

    @property
    def point_wkb(self) -> geoalchemy2.shape.WKBElement:
        return from_shape(shapely.geometry.Point((self.longitude, self.latitude)), srid=4326)

    @property
    def name_prefix(self):
        """The first three letters of the first three words, in lower case"""
        return '.'.join(self._name_prefix_parts())

    def _name_prefix_parts(self) -> Iterable[str]:
        return map(lambda w: re.sub(r'[\W_]', '', w)[0:3], self.name.lower().split()[0:3])

    def tstore_pool_kwargs(self) -> Dict[str, Any]:
        source_short_name = one(set(ctx.source_short_name for ctx in self.contexts))
        short_name = source_short_name + ''.join(self._name_prefix_parts())
        parent_id = one(set(ctx.parent_id for ctx in self.contexts))
        kwargs = {
            'name': self.name,
            'entrance': self.point_wkb,
            'short_name': short_name,
            'status_comment': f"Generated from {source_short_name}",
            'markdown': '',
            'parent_id': parent_id,
        }
        return kwargs


def fetch_one(source: Source) -> sstore.UrlFetch:
    response = requests.get(source.url)
    if response.status_code != 200:
        raise ValueError(f"Bad Response {source}, {response}")
    return sstore.UrlFetch(
        source_short_name=source.short_name, url=source.url, response=response.text)


def add_url_fetch_to_session(session: sqlalchemy.orm.Session, new_fetch: sstore.UrlFetch):
    prev_fetch = session.query(sstore.UrlFetch).filter_by(url=new_fetch.url).order_by(
        sstore.UrlFetch.created_timestamp.desc()).first()
    if prev_fetch and prev_fetch.response == new_fetch.response:
        prev_fetch.unmodified_timestamp = datetime.utcnow()
    else:
        session.add(new_fetch)


def fetch(session: sqlalchemy.orm.Session, sources: List[Source]):
    for source in sources:
        new_fetch = fetch_one(source)
        add_url_fetch_to_session(session, new_fetch)
    session.commit()


@scrape_cli.command('fetch')
def fetch_command():
    fetch(make_session_from_flask_config(), SOURCES)


timestamp_str = datetime.now().strftime("%Y%m%dT%H%M%S")


converter = cattrs.GenConverter()
converter.register_structure_hook(datetime, lambda d, t: datetime.fromisoformat(d))
converter.register_unstructure_hook(datetime, lambda d: d.isoformat())


@scrape_cli.command('dump')
@click.option('--url-fetch-json-path', default=f'url-fetch-{timestamp_str}.jsonl')
@click.option('--entity-extract-json-path', default=f'entity-extract-{timestamp_str}.jsonl')
def dump_command(url_fetch_json_path, entity_extract_json_path):
    session = make_session_from_flask_config()

    def to_json_line(obj) -> str:
        return json.dumps(converter.unstructure(obj), indent=None, separators=(',', ':')) + "\n"

    fetch_file = open(url_fetch_json_path, 'w')
    for fetch in session.query(sstore.UrlFetch).all():
        fetch_file.write(to_json_line(fetch))

    extract_file = open(entity_extract_json_path, 'w')
    for extract in session.query(sstore.EntityExtract).all():
        extract_file.write(to_json_line(extract))


@scrape_cli.command('load')
@click.option('--url-fetch-json-path')
@click.option('--entity-extract-json-path')
def load_command(url_fetch_json_path, entity_extract_json_path):
    assert url_fetch_json_path or entity_extract_json_path
    session = make_session_from_flask_config()

    if url_fetch_json_path:
        for line in open(url_fetch_json_path).readlines():
            fetch = converter.structure(json.loads(line), sstore.UrlFetch)
            session.add(fetch)

    if entity_extract_json_path:
        for line in open(entity_extract_json_path).readlines():
            extract = converter.structure(json.loads(line), sstore.EntityExtract)
            session.add(extract)

    session.commit()


def make_session_from_flask_config() -> sqlalchemy.orm.Session:
    return sstore.make_session(flask.current_app.config['SCRAPER_DATABASE_URI'])


@attrs.frozen()
class PlaceSearcher:
    parent_place: tstore.Place

    def _descendant_names(self, place: tstore.Place) -> Iterable[Tuple[str, tstore.Place]]:
        for child in place.child_places:
            yield (child.name.lower(), child)
            yield from self._descendant_names(child)

    def find_by_name(self, name: str) -> Optional[tstore.Place]:
        descendant_names_pairs = list(self._descendant_names(self.parent_place))
        descendant_names = dict(descendant_names_pairs)
        if len(descendant_names_pairs) != len(descendant_names):
            counts = collections.Counter(name_lower for name_lower, _ in descendant_names_pairs)
            dups = {name_lower: cnt for name_lower, cnt in counts.items() if cnt > 1}
            raise ValueError(f"Duplicate name in {self.parent_place.short_name}? Dups: {dups}")
        return descendant_names.get(name.lower())

    def find_by_short_name(self, short_name: str) -> tstore.Place:
        return tstore.db.session.query(tstore.Place).filter_by(short_name=short_name).one()

    def _places_recursive(self, place: Optional[tstore.Place] = None) -> Iterable[tstore.Place]:
        if place is None:
            place = self.parent_place

        yield place
        for child_place in place.child_places:
            yield from self._places_recursive(child_place)

    def get_all_pools(self) -> Iterable[PoolFrozen]:
        for place in self._places_recursive():
            yield from tstore_to_pools(place.child_pools)

    def get_all_clubs(self) -> Iterable[tstore.Club]:
        for place in self._places_recursive():
            yield from place.child_clubs


def tstore_to_pools(tstore_pools: Iterable[tstore.Pool]) -> Iterable[PoolFrozen]:
   for p in tstore_pools:
       if p.entrance is None:
           warnings.warn(f"Skipping pool without entrance geometry: {p.name}", ScraperWarning)
           continue
       entrance_shapely = p.entrance_shapely
       yield PoolFrozen(entrance_shapely.y, entrance_shapely.x, p.name, [SourceContext(
           'tstore', parent_id=p.parent.id, tstore_pool=p)])


def extract_cuga(parent_place: tstore.Place, fetch: sstore.UrlFetch) -> List[sstore.EntityExtract]:
    place_searcher = PlaceSearcher(parent_place)
    markdownify_converter = MarkdownConverter()
    results = []
    soup = bs4.BeautifulSoup(fetch.response, 'html.parser')
    parts = soup.find_all('div', attrs={'data-vc-content': '.vc_tta-panel-body'})
    for part in parts:
        name = one(part.find_all('span', class_='vc_tta-title-text')).string
        content = part.find_all('div', class_='vc_tta-panel-body')
        if len(content) != 1:
            raise ValueError(f"Expected one panel body")
        content = content[0]
        content_md = markdownify_converter.convert_soup(content).strip()
        p_blocks = content.find_all('p')
        if len(p_blocks) == 1 and re.search(r'no.*clubs here', p_blocks[0].text):
            continue
        tstore_place = place_searcher.find_by_name(name)
        if tstore_place is None:
            print(f"Can't find place {name}")  # Change to some kind of warning
            continue
        entity = sstore.EntityExtract(
            source_short_name=fetch.source_short_name,
            url=fetch.url,
            place_short_name=tstore_place.short_name,
            markdown_content=content_md,
        )
        results.append(entity)
    return results


def extract_sauwhf(parent_place: tstore.Place, fetch: sstore.UrlFetch) -> List[
    sstore.EntityExtract]:
    place_searcher = PlaceSearcher(parent_place)
    markdownify_converter = MarkdownConverter()
    results = []
    soup = bs4.BeautifulSoup(fetch.response, 'html.parser')
    for heading in soup.find_all('div', attrs={'data-widget_type': 'heading.default'}):
        heading_title = heading.get_text(' ', strip=True)
        if re.search(r'(get our news|membership|tournaments|about|SAUWHF)', heading_title,
                     flags=re.IGNORECASE):
            continue
        province_name = re.sub(r'\s+clubs', '', heading_title, flags=re.IGNORECASE)
        province_head_section = heading.find_parent('section')
        province_clubs_section = province_head_section.find_next_sibling('section')
        if not province_clubs_section:
            raise ValueError(f"Couldn't find club info for {heading_title}")
        content_md = (markdownify_converter.convert_soup(province_head_section).strip() + '\n\n' +
            markdownify_converter.convert_soup(province_clubs_section).strip())
        tstore_place = place_searcher.find_by_name(province_name)
        if tstore_place is None:
            print(f"Can't find place {province_name}")  # Change to some kind of warning
            continue
        entity = sstore.EntityExtract(
            source_short_name=fetch.source_short_name,
            url=fetch.url,
            place_short_name=tstore_place.short_name,
            markdown_content=content_md,
        )
        results.append(entity)
    return results


@attrs.frozen()
class GbUwhFeed:
    source: 'GbUwhFeed.Source'
    clubs: List['GbUwhFeed.Club']

    @attrs.frozen()
    class Source:
        name: str
        icon: str

    @attrs.frozen()
    class Club:
        unique_id: str
        name: str
        region: str
        sessions: "List[GbUwhFeed.ClubSession]"
        logo: Optional[str] = None
        facebook: Optional[str] = None
        description: Optional[str] = None
        website: Optional[str] = None
        twitter: Optional[str] = None
        member_count: Optional[int] = None
        instagram: Optional[str] = None

    @attrs.frozen()
    class ClubSession:
        day: str
        latitude: float
        longitude: float
        location_name: str
        type: str
        title: str
        start_time: str
        end_time: str


gbuwhfeed_converter = cattrs.GenConverter(forbid_extra_keys=True)


def get_source_context(club: GbUwhFeed.Club, session: GbUwhFeed.ClubSession, source: Source,
                       region_place: tstore.Place):
    return SourceContext(source.short_name, club_name=club.name, session=session,
                         parent_id=region_place.id, parent_short_name=region_place.short_name)


def get_pool(session: GbUwhFeed.ClubSession) -> PoolFrozen:
    return PoolFrozen(
        session.latitude,
        session.longitude,
        session.location_name,
    )


@attrs.define(frozen=True, order=True, eq=True)
class MetersPools:
    """Two pools and the distance between them, used to debug two instances of a pool with
    different names."""
    meters: float
    pool_1: PoolFrozen
    pool_2: PoolFrozen

    @staticmethod
    def build(pool_1: PoolFrozen, pool_2: PoolFrozen) -> 'MetersPools':
        return MetersPools(distance(pool_1.point_frozen, pool_2.point_frozen).meters, pool_1, pool_2)


def cluster_points(points: Iterable[PointFrozen], distance_meters: float) ->\
        List[Set[PointFrozen]]:
    point_cluster = {pt: {pt} for pt in points}
    for pt1, pt2 in itertools.combinations(points, 2):
        if distance(pt1, pt2).meters < distance_meters:
            new_cluster = {*point_cluster[pt1], *point_cluster[pt2]}
            for pt in new_cluster:
                point_cluster[pt] = new_cluster
    clusters_by_id = {id(cluster): cluster for cluster in point_cluster.values()}
    return list(clusters_by_id.values())


def group_by_distance(pools: Iterable[PoolFrozen], distance_meters: float) -> List[List[PoolFrozen]]:
    """Sorts pools into groups separated by at least given distance."""
    points = set(p.point_frozen for p in pools)
    point_clusters = cluster_points(points, distance_meters)
    assert sorted(points) == sorted(itertools.chain(*point_clusters))
    # Map from every point tuple to a list shared by points within distance_meters.
    pool_group_by_point: Dict[PointFrozen: List[PoolFrozen]] = dict()
    list_of_pool_groups = []
    for point_cluster in point_clusters:
        # Make a new empty list object. It will be referenced once from list_of_pool_groups and
        # once for each point in pool_group_by_point.
        pool_group: List[PoolFrozen] = []
        list_of_pool_groups.append(pool_group)
        for pt in point_cluster:
            pool_group_by_point[pt] = pool_group
    for pool in pools:
        pool_group_by_point[pool.point_frozen].append(pool)
    return list_of_pool_groups


def pool_distances_m(pools: Iterable[PoolFrozen]) -> List[MetersPools]:
    """Return the distance between every pair of the given pools."""
    distances = []
    for p1, p2 in itertools.combinations(pools, 2):
        distances.append(MetersPools.build(p1, p2))
    return distances


class ScraperWarning(UserWarning):
    pass


class RegionNotFoundWarning(ScraperWarning):
    pass


class FeedContainsNonHttpsUrl(ScraperWarning):
    pass


class ScraperError(ValueError):
    pass


class PoolRegionChanged(ScraperError):
    pass


@attrs.frozen()
class ProblemAccumulator:
    logger: Optional[logging.Logger] = attrs.field(factory=prefect.get_run_logger)

    def raise_(self, err: ScraperError):
        raise err

    def warn(self, msg: str, err_class: Type):
        if self.logger:
            self.logger.warning(msg)
        else:
            warnings.warn(msg, err_class)


def parse_and_extract_gbfeed(uk_place: tstore.Place, fetch: sstore.UrlFetch, problems: Optional[
    ProblemAccumulator] = None) -> List[sstore.EntityExtract]:
    fetch_json = json.loads(fetch.response)
    feed: GbUwhFeed = gbuwhfeed_converter.structure(fetch_json, GbUwhFeed)
    if not problems:
        problems = ProblemAccumulator()
    return extract_gbfeed(uk_place, feed, fetch.unmodified_timestamp, problems)


def merge_pools(pool_group: List[PoolFrozen], problems: ProblemAccumulator) -> PoolFrozen:
    if len(pool_group) == 1:
        return one(pool_group)
    furthest = max(pool_distances_m(pool_group))
    if furthest.meters > 10:
        problems.warn(f"Pools more than 10 meters apart: {furthest}", ScraperWarning)
    name_by_prefix = {p.name_prefix: p for p in pool_group}
    if len(name_by_prefix) > 1:
        names = ', '.join(f"'{pool.name}'" for pool in name_by_prefix.values())
        problems.warn(f"Nearby pools with different names: {names}", ScraperWarning)

    combined_contexts = itertools.chain.from_iterable(pool.contexts for pool in pool_group)

    # Return the min pool. It doesn't make semantic sense but is deterministic.
    return attr.evolve(min(pool_group), contexts=list(combined_contexts))


def _format_time(feed_time: str) -> str:
    return re.sub(r":00\Z", "", feed_time)


_DAY_MAP = {
    "monday": "Mondays",
    "tuesday": "Tuesdays",
    "wednesday": "Wednesdays",
    "thursday": "Thursdays",
    "friday": "Fridays",
    "saturday": "Saturdays",
    "sunday": "Sundays",
}


_TYPE_MAP = {
    "junior": "juniors",
    "student": "students",
}


def _format_title(session: GbUwhFeed.ClubSession) -> str:
    title_parts = [session.title]
    if session.day not in session.title.lower():
        title_parts.append(f" on {_DAY_MAP[session.day]}")
    if session.type != "adult" and session.type not in session.title.lower():
        title_parts.append(f" for {_TYPE_MAP[session.type]}")
    return "".join(title_parts)


def make_tstore_club(club: GbUwhFeed.Club, session_pool_to_pool_shortname: Mapping[PoolFrozen, str],
                     feed: GbUwhFeed, source: Source, place: tstore.Place,
                     problems: ProblemAccumulator) -> tstore.Club:
    """Returns a transient/detached tstore.Club from information in the feed."""
    markdown_lines = []
    if club.description:
        markdown_lines.append(club.description)
        markdown_lines.append("")
    for link_attr in ('website', 'facebook', 'twitter', 'instagram'):
        link_str_value = getattr(club, link_attr)
        if link_str_value:
            parsed_url = urlparse(link_str_value)
            if parsed_url.scheme not in ("https", "http"):
                problems.warn(f"{link_attr} expected https but {club.name} contains "
                              f"'{link_str_value}'", FeedContainsNonHttpsUrl)
                continue
            markdown_lines.append(f"* <{link_str_value}>")
    if club.sessions:
        markdown_lines.append("\nSessions: \n")
    for session in club.sessions:
        pool = get_pool(session)
        short_name = session_pool_to_pool_shortname[pool]

        markdown_lines.append(f"* {_format_title(session)} from "
                              f"{_format_time(session.start_time)} to "
                              f"{_format_time(session.end_time)} at [[{short_name}]]")

    short_name = ''.join(map(lambda w: re.sub(r'[\W_]', '', w)[0:5], club.name.lower().split()[
                                                                     0:3]))

    return tstore.Club(
        name=club.name,
        short_name=short_name,
        markdown='\n'.join(markdown_lines),
        # I want this Club object to remain in the transient/detached state so that it isn't
        # implicitly added to the database or validated. Setting `parent` makes it pending due to
        # cascading so instead set `parent_id`.
        parent_id=place.id,
        status_comment=f'Imported from {feed.source.name}',
        source_short_name=source.short_name,
        source_key=club.unique_id,
        logo_url=club.logo or None,
    )


@attrs.frozen(auto_attribs=True)
class ClubSyncResults:
    to_add: List[tstore.Club] = attrs.field(factory=list)
    to_del: List[tstore.Club] = attrs.field(factory=list)
    updated: List[tstore.Club] = attrs.field(factory=list)
    unmodified: List[tstore.Club] = attrs.field(factory=list)

    def summary(self) -> str:
        return (
            f"to_add: {', '.join(c.name for c in self.to_add)}\n"
            f"to_del: {', '.join(c.name for c in self.to_del)}\n"
            f"updated: {', '.join(c.name for c in self.updated)}\n"
            f"unmodified count: {len(self.unmodified)}\n")


def gbuwh_club_sync(old_clubs: Iterable[tstore.Club], new_clubs: Iterable[tstore.Club]) -> \
        ClubSyncResults:
    old_clubs_not_seen = set(old_clubs)
    old_clubs_by_source_key = {club.source_key: club for club in old_clubs}

    results = ClubSyncResults()
    new_source_keys = set()
    for new_club in new_clubs:
        new_source_keys.add(new_club.source_key)
        old_club = old_clubs_by_source_key.get(new_club.source_key, None)
        if old_club is None:
            results.to_add.append(new_club)
        else:
            old_clubs_not_seen.remove(old_club)
            is_updated, _ = sync.sync_club(new_club, old_club,
                                           ignore_columns=('id', 'short_name', 'status_date'))
            if is_updated:
                results.updated.append(old_club)
            else:
                results.unmodified.append(old_club)
    results.to_del.extend(old_clubs_not_seen)
    return results


@attrs.frozen(auto_attribs=True)
class PoolSyncResults:
    # Maybe combine this and ClubSyncResults with some kind of type templated class
    to_add: List[tstore.Pool] = attrs.field(factory=list)
    to_del: List[tstore.Pool] = attrs.field(factory=list)
    updated: List[tstore.Pool] = attrs.field(factory=list)
    unmodified: List[tstore.Pool] = attrs.field(factory=list)

    def summary(self) -> str:
        return (
            f"to_add: {', '.join(c.name for c in self.to_add)}\n"
            f"to_del: {', '.join(c.name for c in self.to_del)}\n"
            f"updated: {', '.join(c.name for c in self.updated)}\n"
            f"unmodified count: {len(self.unmodified)}\n")


def gbuwh_sync_pools(gbsource, grouped_union_of_pools: List[List[PoolFrozen]],
                     problems: ProblemAccumulator) -> PoolSyncResults:
    results = PoolSyncResults()
    for pool_group in grouped_union_of_pools:
        pool_group.sort(key=lambda pool: one(pool.source_short_name))
        sources = tuple(one(pool.source_short_name) for pool in pool_group)
        if sources == ('tstore',):
            results.to_del.append(one(one(pool_group).contexts).tstore_pool)
        elif sources == (gbsource.short_name, ):
            feed_pool_frozen: PoolFrozen = one(pool_group)
            new_pool = tstore.Pool(**feed_pool_frozen.tstore_pool_kwargs())
            results.to_add.append(new_pool)
        elif sources == (gbsource.short_name, 'tstore'):
            feed_pool_frozen, tstore_pool_frozen = pool_group
            existing_pool = one(tstore_pool_frozen.contexts).tstore_pool
            new_pool = tstore.Pool(**feed_pool_frozen.tstore_pool_kwargs())
            updated_columns = sync.sync_pool_objects(new_pool=new_pool, old_pool=existing_pool,
                                                     ignore_columns=('id', 'short_name',
                                                                     'status_date', 'parent_id'))
            if new_pool.parent_id != existing_pool.parent_id:
                new_parent_short_name = one(set(ctx.parent_short_name for ctx in
                                                feed_pool_frozen.contexts))
                problems.raise_(PoolRegionChanged(f"Pool {existing_pool.name} region changed from "
                                        f"{existing_pool.parent.short_name} to "
                                        f"{new_parent_short_name}"))
            if updated_columns:
                results.updated.append(existing_pool)
            else:
                results.unmodified.append(existing_pool)
        else:
            raise ValueError(f'Unexpected pool group sources: {pool_group}')
    return results


def extract_gbfeed(uk_place: tstore.Place, feed: GbUwhFeed, fetch_timestamp: datetime, problems: ProblemAccumulator) \
        -> List[sstore.EntityExtract]:
    region_map = {"South West": "South", "South East": "South"}
    gbsource = SOURCE_BY_SHORT_NAME['gbuwh-feed-clubs']
    place_searcher = PlaceSearcher(uk_place)
    clubs_by_region = defaultdict(list)
    for club in feed.clubs:
        if 'Internal Admin only' in club.name:
            continue
        clubs_by_region[club.region].append(club)
    # For feed pools with identical lat, lng, name create a single `PoolFrozen` object.
    pools_by_value: Dict[PoolFrozen, PoolFrozen] = {}  # Pools by lat,lng,name.
    for region, clubs in list(clubs_by_region.items()):
        place_name = region_map.get(region, region)
        region_place = place_searcher.find_by_name(place_name)
        if region_place is None:
            problems.warn(f"Region {region} mapped to place {place_name}, not found in tstore. "
                          f"Add it manually. Contains clubs: {', '.join(c.name for c in clubs)}",
                          RegionNotFoundWarning)
            del clubs_by_region[region]
            continue
        for club in clubs:
            for session in club.sessions:
                pool = get_pool(session)
                pool_context = get_source_context(club, session, gbsource, region_place)
                pools_by_value.setdefault(pool, pool).contexts.append(pool_context)

    # Some pools are represented in the feed by slightly different coordinates and names. Cluster
    # them and look for mistakes.
    pools_grouped_by_distance = group_by_distance(pools_by_value.values(), 200)
    check_for_similar_names_in_different_groups(pools_grouped_by_distance, problems)
    feed_unique_pools = [merge_pools(pool_group, problems) for pool_group in
                         pools_grouped_by_distance]
    for pool in feed_unique_pools:
        parents = set(ctx.parent_short_name for ctx in pool.contexts)
        if len(parents) != 1:
            problems.warn(f"Pool in multiple regions: {pool}", ScraperWarning)

    # Now cluster the unique pools in the feed with the pools in the tstore database.
    orm_tstore_pools = list(place_searcher.get_all_pools())
    # This call to `group_by_distance` is expected to return groups with no more than one pool
    # from feed_unique_pools and no more than one pool from orm_tstore_pools.
    grouped_union_of_pools = group_by_distance([*orm_tstore_pools, *feed_unique_pools], 200)

    # Add/delete/update the tstore database to be in sync with feed_unique_pools.
    pool_sync = gbuwh_sync_pools(gbsource, grouped_union_of_pools, problems)
    print(pool_sync.summary())
    tstore.db.session.add_all(pool_sync.to_add)
    for pool in pool_sync.to_del:
        tstore.db.session.delete(pool)
    tstore.db.session.commit()

    committed_pools_by_point: Mapping[PointFrozen: PoolFrozen] = {
        p.point_frozen: p for p in place_searcher.get_all_pools()}
    session_pool_to_pool_shortname: MutableMapping[PoolFrozen: str] = {}
    for unique_pool in feed_unique_pools:
            tstore_context = one(committed_pools_by_point[unique_pool.point_frozen].contexts)
            pool_short_name = tstore_context.tstore_pool.short_name
            for ctx in unique_pool.contexts:
                session_pool_to_pool_shortname[get_pool(ctx.session)] = pool_short_name

    new_tstore_clubs = []
    for region, clubs in clubs_by_region.items():
        place_name = region_map.get(region, region)
        region_place = place_searcher.find_by_name(place_name)
        assert region_place is not None
        for club in clubs:
            new_tstore_clubs.append(make_tstore_club(club, session_pool_to_pool_shortname,
                                                     feed, gbsource, region_place, problems))
    all_existing_clubs = list(place_searcher.get_all_clubs())
    club_sync = gbuwh_club_sync(old_clubs=all_existing_clubs, new_clubs=new_tstore_clubs)
    print(club_sync.summary())
    tstore.db.session.add_all(club_sync.to_add)
    for club in club_sync.to_del:
        tstore.db.session.delete(club)

    tstore_source = _get_or_add_tstore_source('gbuwh-feed-clubs')
    tstore_source.sync_timestamp = fetch_timestamp
    tstore_source.name = feed.source.name
    tstore_source.logo_url = feed.source.icon
    tstore_source.place_id = uk_place.id

    tstore.db.session.commit()

    return []


def _get_or_add_tstore_source(source_short_name: str) -> tstore.Source:
    results = tstore.Source.query.filter_by(source_short_name='gbuwh-feed-clubs').all()
    if results:
        return one(results)
    else:
        new_source = tstore.Source(source_short_name=source_short_name)
        tstore.db.session.add(new_source)
        return new_source


def check_for_similar_names_in_different_groups(pools_grouped_by_distance, problems: ProblemAccumulator):
    name_prefix_to_pool = {}
    for pool_group in pools_grouped_by_distance:
        for pool in pool_group:
            if pool.name_prefix in name_prefix_to_pool:
                other_pool = name_prefix_to_pool[pool.name_prefix]
                pool_distance = one(pool_distances_m([pool, other_pool]))
                problems.raise_(ScraperError(f"Similar names in different distance groups: "
                                         f"{pool_distance}"))
        for pool in pool_group:
            name_prefix_to_pool[pool.name_prefix] = pool


# Source values in SOURCES are closely related to rows in the tstore.Source table.
SOURCES = [
    Source("cuga-uwh", "http://cuga.org/en/where-and-when-uwh/", "ca", extract_cuga),
    Source("sauwhf", "https://sauwhf.co.za/clubs/", "za", extract_sauwhf),
    Source("gbuwh-feed-clubs", "https://www.gbuwh.co.uk/feeds/clubs", "uk",
           parse_and_extract_gbfeed),
]

SOURCE_BY_SHORT_NAME = {source.short_name: source for source in SOURCES}
# SOURCES could be an enum, but I'd rather have it in a representation that is closer to being
# stored as data instead of in the source code.
assert len(SOURCES) == len(SOURCE_BY_SHORT_NAME)


def extract():
    session = make_session_from_flask_config()
    world_place = tstore.Place.query.filter_by(short_name='world').one()
    world_place_searcher = PlaceSearcher(world_place)
    utc_now = datetime.utcnow()
    for fetch in session.query(sstore.UrlFetch).filter_by(extract_timestamp=None).all():
        source = SOURCE_BY_SHORT_NAME[fetch.source_short_name]
        parent_place = world_place_searcher.find_by_short_name(source.place_short_name)
        for extract in source.extractor(parent_place, fetch):
            prev_extract = session.query(sstore.EntityExtract).filter_by(
                url=extract.url, place_short_name=extract.place_short_name).order_by(
                sstore.EntityExtract.created_timestamp.desc()).first()
            if prev_extract and prev_extract.markdown_content == extract.markdown_content:
                prev_extract.unmodified_timestamp = utc_now
            else:
                extract.created_timestamp = utc_now
                extract.unmodified_timestamp = utc_now
                session.add(extract)
        fetch.extract_timestamp = utc_now
    session.commit()


@scrape_cli.command('extract')
def extract_command():
    extract()


@attr.define
class ExtractNewest:
    with_comment_id: SortedList = attr.ib(factory=SortedList)
    without_comment_id: SortedList = attr.ib(factory=SortedList)

    def add(self, extract: sstore.EntityExtract):
        if extract.place_comment_id is None:
            self.without_comment_id.add(extract)
        else:
            self.with_comment_id.add(extract)

    def get_newest_without_comment_id(self) -> Tuple[Optional[sstore.EntityExtract], Optional[
        sstore.EntityExtract]]:
        """Returns the newest extract without a place_comment_id and the newest with a
        place_comment_id, assuming the former is newer."""
        if not self.without_comment_id:
            # No extract without a comment. This is likely the common case when no new extract
            # has been found.
            return None, None
        newest_without_comment = self.without_comment_id[-1]
        if not self.with_comment_id:
            # There is an extract without a comment_id and none with a comment_id. Maybe this is
            # the first extract.
            return newest_without_comment, None
        newest_with_comment = self.with_comment_id[-1]
        if newest_without_comment > newest_with_comment:
            # There is a new extract without comment_id and an older one with a comment_id. This
            # is likely the common case when a new extract is added.
            return newest_without_comment, newest_with_comment
        else:
            # There is an extract without comment_id and an newer one with a comment_id. This
            # is likely an uncommon case when an old extract was replaced before it was used to
            # make a comment.
            return None, None


@attr.define
class ExtractDict:
    by_url_place: Mapping[Tuple[str, str], ExtractNewest] = attr.ib(factory=lambda: defaultdict(ExtractNewest))

    def add(self, extract: sstore.EntityExtract):
        self.by_url_place[(extract.url, extract.place_short_name)].add(extract)


@scrape_cli.command('comment')
def comment_command():
    session = make_session_from_flask_config()
    world_place = tstore.Place.query.filter_by(short_name='world').one()
    world_place_searcher = PlaceSearcher(world_place)
    comment_to_extract = {}
    extract_dict = ExtractDict()
    for extract in session.query(sstore.EntityExtract).all():
        extract_dict.add(extract)
    for (url, place_short_name), newest in extract_dict.by_url_place.items():
        new_without_comment, older_with_comment = newest.get_newest_without_comment_id()
        if new_without_comment:
            place = world_place_searcher.find_by_short_name(place_short_name)
            content = ''
            if older_with_comment:
                content = 'Was\n\n' + older_with_comment.markdown_content + '\n\n'
            content += 'Now\n\n' + new_without_comment.markdown_content
            comment = tstore.PlaceComment(
                source=f"Fetched from {url}",
                content_markdown=content,
                place=place,
            )
            comment_to_extract[comment] = new_without_comment
            print(f"Comment added to {place.short_name}:\n{comment}")
    tstore.db.session.add_all(comment_to_extract.keys())
    tstore.db.session.commit()
    for comment, extract in comment_to_extract.items():
        tstore.db.session.refresh(comment)
        assert comment.id
        extract.place_comment_id = comment.id
    session.commit()


@scrape_cli.command('extract-gbuwh')
@click.argument('urlfetch_jsonl_path')
def extract_gbuwh(urlfetch_jsonl_path):
    uk_place = tstore.Place.query.filter_by(short_name='uk').one()

    json_line = one(open(urlfetch_jsonl_path).readlines())
    url_fetch = converter.structure(json.loads(json_line), sstore.UrlFetch)

    parse_and_extract_gbfeed(uk_place, url_fetch, problems=ProblemAccumulator(logger=None))


@scrape_cli.command('run-gb-dataflow')
@click.option('--fetch-timestamp')
def run_gb_dataflow(fetch_timestamp: str = None):
    import tourist.scripts.dataflow
    tourist.scripts.dataflow.run_gb_fetch_and_sync(fetch_timestamp=fetch_timestamp)

