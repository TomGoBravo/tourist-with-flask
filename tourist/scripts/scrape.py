import collections
import itertools
import json
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
from typing import Optional
from typing import Set
from typing import Tuple

import attr
import bs4
import cattrs
import click
import flask
import geoalchemy2.shape
import geopy.point
import geopy.distance
import shapely.geometry
import sqlalchemy.orm

import requests
from flask.cli import AppGroup
from geoalchemy2.shape import from_shape
from markdownify import MarkdownConverter
from more_itertools import one
from sortedcontainers import SortedList

from tourist.models import sstore
import attrs

from tourist.models import tstore

scrape_cli = AppGroup('scrape')


@attrs.frozen()
class Source:
    """Scraper source configuration"""
    short_name: str
    url: str
    place_short_name: str
    extractor: Callable[[tstore.Place, sstore.UrlFetch], List[sstore.EntityExtract]]


@attrs.frozen()
class ClubFeed:
    """The structure used by the GB UWH JSON club feed."""
    source: 'ClubFeed.Source'
    clubs: List['ClubFeed.Club']

    @attrs.frozen()
    class Source:
        name: str
        icon: str

    @attrs.frozen()
    class Club:
        name: str
        logo: str
        place_short_name: str
        sessions: List['ClubFeed.ClubSession']

    @attrs.frozen()
    class ClubSession:
        day: str
        type: str
        title: str
        start_time: str
        end_time: str
        pool_short_name: str


# latitude, longitude: the order expected by geopy.distance. This is opposite the order used by
# shapely and WKT!
# shapely and geopy points are mutable and not hashable so I'm defining a very simple type
# for locations. Altitude is NOT included because spatialite seems to silently drop WKB
# coordinates with Z; it took me a while to work this out.
PointTuple = Tuple[float, float]


@attrs.frozen(eq=True, order=True)
class SourceContext:
    source_short_name: str
    club_name: str = ''
    session: Optional['GbUwhFeed.ClubSession'] = None
    parent_short_name: str = ''
    region: str = ''
    tstore_pool: Optional[tstore.Pool] = None


@attrs.frozen(eq=True, order=True)
class Pool:
    """A pool location and name, used by code syncs pools from tstore and scraped sources."""
    latitude: float
    longitude: float
    name: str
    contexts: List[SourceContext] = attrs.field(eq=False, order=False, factory=list)

    @property
    def source_short_name(self) -> List[str]:
        return sorted(set(c.source_short_name for c in self.contexts))

    @property
    def point(self):
        return geopy.point.Point(latitude=self.latitude, longitude=self.longitude)

    @property
    def point_tuple(self) -> PointTuple:
        """Return the point as a bare tuple. Unlike Point this is immutable and hashable."""
        return (self.latitude, self.longitude)

    @property
    def point_wkb(self) -> geoalchemy2.shape.WKBElement:
        return from_shape(shapely.geometry.Point((self.longitude, self.latitude)), srid=4326)

    @property
    def name_prefix(self):
        """The first three letters of the first three words, in lower case"""
        return '.'.join(self._name_prefix_parts())

    def _name_prefix_parts(self) -> Iterable[str]:
        return map(lambda w: re.sub(r'[\W_]', '', w)[0:3], self.name.lower().split()[0:3])

    def sqlalchemy_kwargs(self) -> Dict[str, Any]:
        kwargs = {
            'name': self.name,
            'entrance': self.point_wkb,
        }
        source_short_name = one(set(ctx.source_short_name for ctx in self.contexts))
        short_name = source_short_name + ''.join(self._name_prefix_parts())
        kwargs['short_name'] = short_name
        kwargs['status_comment'] = f"Generated from {source_short_name}"
        kwargs['status_date'] = date.today().isoformat()
        return kwargs


def fetch(session: sqlalchemy.orm.Session, sources: List[Source]):
    for source in sources:
        response = requests.get(source.url)
        if response.status_code != 200:
            raise ValueError(f"Bad Response {source}, {response}")
        prev_fetch = session.query(sstore.UrlFetch).filter_by(url=source.url).order_by(
            sstore.UrlFetch.created_timestamp.desc()).first()
        if prev_fetch and prev_fetch.response == response.text:
            prev_fetch.unmodified_timestamp = datetime.utcnow()
        else:
            new_fetch = sstore.UrlFetch(source_short_name=source.short_name, url=source.url,
                                        response=response.text)
            session.add(new_fetch)
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

    def _child_pools(self, place: tstore.Place) -> Iterable[Pool]:
        p: tstore.Pool
        for p in place.child_pools:
            if p.entrance is None:
                warnings.warn(f"Skipping pool without entrance geometry: {p.name}", ScraperWarning)
                continue
            entrance_shapely = p.entrance_shapely
            yield Pool(entrance_shapely.y, entrance_shapely.x, p.name, [SourceContext(
                'tstore', parent_short_name=place.short_name, tstore_pool=p)])
        for child_place in place.child_places:
            yield from self._child_pools(child_place)

    def get_all_pools(self) -> Iterable[Pool]:
        yield from self._child_pools(self.parent_place)


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
        logo: str
        region: str
        sessions: List['GbUwhFeed.ClubSession']

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


# I haven't looked into why ClubSession needs to be explicitly registered, but this makes it work.
cattrs.register_structure_hook(
    ForwardRef("GbUwhFeed.ClubSession"), lambda d, t: cattrs.structure(d, GbUwhFeed.ClubSession),
)


def get_source_context(club: GbUwhFeed.Club, session: GbUwhFeed.ClubSession, source: Source):
    return SourceContext(source.short_name, club_name=club.name, session=session, region=club.region)


def get_pool(session: GbUwhFeed.ClubSession) -> Pool:
    return Pool(
        session.latitude,
        session.longitude,
        session.location_name,
    )


@attrs.define(frozen=True, order=True, eq=True)
class MetersPools:
    """Two pools and the distance between them, used to debug two instances of a pool with
    different names."""
    meters: float
    pool_1: Pool
    pool_2: Pool

    @staticmethod
    def build(pool_1: Pool, pool_2: Pool) -> 'MetersPools':
        return MetersPools(geopy.distance.distance(pool_1.point, pool_2.point).meters, pool_1, pool_2)


def cluster_points(point_tuples: Iterable[PointTuple], distance_meters: float) ->\
        List[Set[PointTuple]]:
    point_cluster = {pt: {pt} for pt in point_tuples}
    for pt1, pt2 in itertools.combinations(point_tuples, 2):
        if geopy.distance.distance(pt1, pt2).meters < distance_meters:
            new_cluster = {*point_cluster[pt1], *point_cluster[pt2]}
            for pt in new_cluster:
                point_cluster[pt] = new_cluster
    clusters_by_id = {id(cluster): cluster for cluster in point_cluster.values()}
    return list(clusters_by_id.values())


def group_by_distance(pools: Iterable[Pool], distance_meters: float) -> List[List[Pool]]:
    point_tuples = set(p.point_tuple for p in pools)
    point_clusters = cluster_points(point_tuples, distance_meters)
    assert sorted(point_tuples) == sorted(itertools.chain(*point_clusters))
    # Map from every point tuple to a list shared by points within distance_meters.
    pool_group_by_point = dict()
    list_of_pool_groups = []
    for point_cluster in point_clusters:
        # Make a new empty list object. It will be referenced once from list_of_pool_groups and
        # once for each point in pool_group_by_point.
        pool_group = []
        list_of_pool_groups.append(pool_group)
        for pt in point_cluster:
            pool_group_by_point[pt] = pool_group
    for pool in pools:
        pool_group_by_point[pool.point_tuple].append(pool)
    return list_of_pool_groups


def pool_distances_m(pools: Iterable[Pool]) -> List[MetersPools]:
    """Return the distance between every pair of the given pools."""
    distances = []
    for p1, p2 in itertools.combinations(pools, 2):
        distances.append(MetersPools.build(p1, p2))
    return distances


class ScraperWarning(UserWarning):
    pass


class ScraperError(ValueError):
    pass


def parse_and_extract_gbfeed(uk_place: tstore.Place, fetch: sstore.UrlFetch) -> List[
    sstore.EntityExtract]:
    fetch_json = json.loads(fetch.response)
    feed: GbUwhFeed = cattrs.structure(fetch_json, GbUwhFeed)
    return extract_gbfeed(uk_place, feed)


def merge_pools(pool_group: List[Pool]) -> Pool:
    if len(pool_group) == 1:
        return one(pool_group)
    furthest = max(pool_distances_m(pool_group))
    if furthest.meters > 10:
        warnings.warn(f"Pools more than 10 meters apart: {furthest}", ScraperWarning)
    name_by_prefix = {p.name_prefix: p for p in pool_group}
    if len(name_by_prefix) > 1:
        names = ', '.join(f"'{pool.name}'" for pool in name_by_prefix.values())
        warnings.warn(f"Nearby pools with different names: {names}", ScraperWarning)

    combined_contexts = itertools.chain.from_iterable(pool.contexts for pool in pool_group)

    # Return the min pool. It doesn't make semantic sense but is deterministic.
    return attr.evolve(min(pool_group), contexts=list(combined_contexts))


def extract_gbfeed(uk_place: tstore.Place, feed: GbUwhFeed) -> List[sstore.EntityExtract]:
    region_map = {"South West": "South", "South East": "South"}
    results = []
    gbsource = SOURCE_BY_SHORT_NAME['gbuwh-feed-clubs']
    place_searcher = PlaceSearcher(uk_place)
    pools_by_value: Dict[Pool, Pool] = {}  # Pools by lat,lng,name.
    # XXX Add region to Pool to catch multiple regions more cleanly?
    clubs_by_region = defaultdict(list)
    for club in feed.clubs:
        clubs_by_region[club.region].append(club)
    for region, clubs in clubs_by_region.items():
        place_name = region_map.get(region, region)
        region_place = place_searcher.find_by_name(place_name)
        if region_place is None:
            warnings.warn(f"Region {region} mapped to place {place_name}, not found in tstore. "
                          f"Add it manually. Contains clubs: {', '.join(c.name for c in clubs)}",
                          ScraperWarning)
            continue
        for club in clubs:
            for session in club.sessions:
                pool = get_pool(session)
                pool_context = get_source_context(club, session, gbsource)
                pools_by_value.setdefault(pool, pool).contexts.append(pool_context)

    pools_grouped_by_distance = group_by_distance(pools_by_value.values(), 200)
    check_for_similar_names_in_different_groups(pools_grouped_by_distance)
    unique_pools = [merge_pools(pool_group) for pool_group in pools_grouped_by_distance]
    for pool in unique_pools:
        regions = set(region_map.get(ctx.region, ctx.region) for ctx in pool.contexts)
        if len(regions) != 1:
            warnings.warn(f"Pool in multiple regions: {pool}", ScraperWarning)

    all_tstore_pools = place_searcher.get_all_pools()
    grouped_union_of_pools = group_by_distance([*all_tstore_pools, *unique_pools], 200)

    for pool_group in grouped_union_of_pools:
        pool_group.sort(key=lambda pool: one(pool.source_short_name))
        sources = tuple(one(pool.source_short_name) for pool in pool_group)
        if sources == ('tstore', ):
            print(f'remove {pool_group}')
            tstore.db.session.delete(one(one(pool_group).contexts).tstore_pool)
        elif sources == (gbsource.short_name, ):
            print(f'add {pool_group}')
            p: Pool = one(pool_group)
            region = one(set(region_map.get(ctx.region, ctx.region) for ctx in p.contexts))
            region_place = place_searcher.find_by_name(region)
            new_pool_args = p.sqlalchemy_kwargs()
            new_pool_args['parent'] = region_place
            new_pool = tstore.Pool(**new_pool_args)
            new_pool.entrance = new_pool_args['entrance']
            tstore.db.session.add(new_pool)
        elif sources == (gbsource.short_name, 'tstore'):
            print(f'update {pool_group}')
            feed_pool, tstore_pool = pool_group
            orm_pool = one(tstore_pool.contexts).tstore_pool
            orm_pool.name = feed_pool.name
            orm_pool.entrance = feed_pool.point_wkb
        else:
            print(f'Unclear what to do with {pool_group}')
    tstore.db.session.commit()

    commited_pools_by_point: Mapping[PointTuple: Pool] = {
        p.point_tuple: p for p in place_searcher.get_all_pools()}
    session_pool_to_tstore_pool = {}
    for unique_pool in unique_pools:
        tstore_pool = one(commited_pools_by_point[unique_pool.point_tuple].contexts).tstore_pool
        for ctx in unique_pool.contexts:
            session_pool_to_tstore_pool[get_pool(ctx.session)] = tstore_pool

    return results


def check_for_similar_names_in_different_groups(pools_grouped_by_distance):
    name_prefix_to_pool = {}
    for pool_group in pools_grouped_by_distance:
        for pool in pool_group:
            if pool.name_prefix in name_prefix_to_pool:
                other_pool = name_prefix_to_pool[pool.name_prefix]
                pool_distance = one(pool_distances_m([pool, other_pool]))
                raise ScraperError(f"Similar names in different distance groups: {pool_distance}")
        for pool in pool_group:
            name_prefix_to_pool[pool.name_prefix] = pool


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


@scrape_cli.command('extract')
def extract_command():
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

    parse_and_extract_gbfeed(uk_place, url_fetch)
