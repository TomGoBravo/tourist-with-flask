import json
import pathlib
import re
import warnings
from datetime import datetime
from typing import Mapping
from typing import Sequence

import attr
import click.testing
import flask
import pytest
import responses
import sqlalchemy.exc
from freezegun import freeze_time
from geoalchemy2 import WKTElement
from more_itertools import one

import tourist
from tourist.models import sstore
from tourist.models import tstore
from tourist.scripts import scrape
from tourist.tests.conftest import path_relative
from tourist.scripts.scrape import GbUwhFeed


# Ignore "sqlalchemy_continuum/unit_of_work.py:263: SAWarning:  ..."
# because it will take a while for them to be fixed. See
# https://github.com/kvesteri/sqlalchemy-continuum/issues/255#issuecomment-1003629992
# and
# https://github.com/kvesteri/sqlalchemy-continuum/issues/263
pytestmark = pytest.mark.filterwarnings("ignore", message=r".*implicitly coercing SELECT")


@responses.activate
def test_fetch():
    source0 = scrape.SOURCES[0]
    responses.add(responses.GET, body="hello world", url=source0.url)

    session = sstore.make_session("sqlite://")

    # First fetch
    created_datetime = datetime.fromisoformat("2022-03-08T21:04:00")
    with freeze_time(created_datetime):
        scrape.fetch(session, [source0])

    fetch = one(session.query(sstore.UrlFetch).all())
    expected_fetch = sstore.UrlFetch(
        source_short_name=source0.short_name,
        url=source0.url,
        created_timestamp=created_datetime,
        unmodified_timestamp=created_datetime,
        response="hello world"
    )
    assert fetch == expected_fetch

    # Fetch again with same response
    fetch_again_datetime = datetime.fromisoformat("2022-03-08T22:04:00")
    with freeze_time(fetch_again_datetime):
        scrape.fetch(session, [source0])
    fetch = one(session.query(sstore.UrlFetch).all())
    expected_fetch = attr.evolve(expected_fetch, unmodified_timestamp=fetch_again_datetime)
    assert fetch == expected_fetch

    # Fetch again with modified response
    responses.add(responses.GET, body="hello world, again", url=source0.url)
    fetch_updated_datetime = datetime.fromisoformat("2022-03-08T23:04:00")
    with freeze_time(fetch_updated_datetime):
        scrape.fetch(session, [source0])
    expected_fetch_again = attr.evolve(expected_fetch, created_timestamp=fetch_updated_datetime,
                                       unmodified_timestamp=fetch_updated_datetime,
                                       response="hello world, again")
    assert [expected_fetch, expected_fetch_again] == list(session.query(
        sstore.UrlFetch).order_by(sstore.UrlFetch.created_timestamp).all())


@attr.define
class ScrapeRunner():
    app: flask.Flask

    def invoke_scrape(self, args: Sequence[str]) -> click.testing.Result:
        data_dir = self.app.config['DATA_DIR']
        cli_runner = self.app.test_cli_runner(mix_stderr=False)
        env = {'TOURIST_ENV': 'development', 'FLASK_APP': 'tourist', 'DATA_DIR': str(data_dir)}
        result = cli_runner.invoke(scrape.scrape_cli, args, env=env, catch_exceptions=False)
        print(result.stdout)
        print(result.stderr)
        assert result.exit_code == 0
        return result


def test_load_and_extract(test_app):
    runner = ScrapeRunner(test_app)
    add_canada(test_app)
    jsonl_path = path_relative('url-fetch-20220308T232323.jsonl')

    runner.invoke_scrape(['load', f'--url-fetch-json-path={str(jsonl_path)}'])
    runner.invoke_scrape(['extract'])

    session = sstore.make_session(test_app.config['SCRAPER_DATABASE_URI'])
    extract: sstore.EntityExtract = one(session.query(sstore.EntityExtract).filter_by(
        place_short_name='cabc').all())
    assert extract.source_short_name == 'cuga-uwh'
    assert extract.url == 'http://cuga.org/en/where-and-when-uwh/'
    assert extract.place_short_name == 'cabc'
    assert extract.markdown_content.startswith('**Port Coquitlam – Rumblefish UWH**')
    assert extract.place_comment_id is None


def test_group_distance():
    # latitude where each 0.01 degrees longitude is 1km
    magic_lat = 26.062468289

    pools = [
        scrape.PoolFrozen(latitude=magic_lat, longitude=0.000, name='a'),
        scrape.PoolFrozen(latitude=magic_lat, longitude=0.009, name='b'),
        scrape.PoolFrozen(latitude=magic_lat, longitude=0.009, name='c'),
        scrape.PoolFrozen(latitude=magic_lat, longitude=0.018, name='d'),
        scrape.PoolFrozen(latitude=magic_lat, longitude=0.029, name='e'),
        scrape.PoolFrozen(latitude=magic_lat, longitude=0.038, name='f'),
        scrape.PoolFrozen(latitude=magic_lat, longitude=0.049, name='g'),
    ]

    pool_groups = scrape.group_by_distance(pools, 1_000)
    pool_names_by_group = []
    for group in pool_groups:
        pool_names_by_group.append(''.join(sorted(p.name for p in group)))
    assert sorted(pool_names_by_group) == ['abcd', 'ef', 'g']


def test_also_group_by_name_normal():
    pools = [
        [scrape.PoolFrozen(0, 0, 'test pool one ignored difference')],
        [scrape.PoolFrozen(0, 1, 'test pool two'), scrape.PoolFrozen(0, 2, 'test pool two')],
        [scrape.PoolFrozen(0, 3, 'test pool one')],
        [scrape.PoolFrozen(0, 4, 'test pool three')],
    ]

    pools_out = scrape.also_group_by_name(pools, scrape.ProblemAccumulator(logger=None))

    pool_lng_by_group = []
    for group in pools_out:
        pool_lng_by_group.append(''.join(sorted(str(p.longitude) for p in group)))
    assert sorted(pool_lng_by_group) == ['03', '12', '4']


def test_also_group_by_name_input_group_of_three():
    pools = [
        [scrape.PoolFrozen(0, 1, 'one'), scrape.PoolFrozen(0, 2, 'two'),
         scrape.PoolFrozen(0, 3, 'three')],
    ]

    with pytest.raises(scrape.ScraperError, match="Expecting only groups of size 1 and 2"):
        scrape.also_group_by_name(pools, scrape.ProblemAccumulator(logger=None))


def test_also_group_by_name_three_with_similar_name():
    pools = [
        [scrape.PoolFrozen(0, 1, 'name one')], [scrape.PoolFrozen(0, 2, 'name one')],
         [scrape.PoolFrozen(0, 3, 'name oneignoredpart')],
    ]

    with pytest.raises(scrape.ScraperError, match="3 pools with similar name"):
        scrape.also_group_by_name(pools, scrape.ProblemAccumulator(logger=None))


def add_uk(test_app, add_pools: bool = True):
    with test_app.app_context():
        world = tstore.Place(name='World', short_name='world', markdown='')
        uk = tstore.Place(name='United Kingdom', short_name='uk', parent=world,
                          region=WKTElement('POLYGON ((4.27 51.03, 4.27 59.56, -10.69 59.56, '
                                            '-10.69 51.03, 4.27 51.03))', srid=4326))
        north = tstore.Place(name='North', short_name='uknorth', parent=uk,
                             region=WKTElement('POLYGON ((4.27 51.03, 4.27 59.56, -10.69 59.56, '
                                               '-10.69 51.03, 4.27 51.03))', srid=4326))
        london = tstore.Place(name='London', short_name='uklon', parent=uk,
                              region=WKTElement('POLYGON ((4.27 51.03, 4.27 59.56, -10.69 59.56, '
                                                '-10.69 51.03, 4.27 51.03))', srid=4326))
        tstore.db.session.add_all([world, uk, north, london])

        if add_pools:
            poolden = tstore.Pool(name='Denton Wellness Center', short_name='denton', parent=north,
                                  markdown='', entrance=WKTElement('POINT(-2.1140 53.4575)',
                                                                   srid=4326))
            poolgeo2 = tstore.Pool(name='Metro Pool', short_name='poolgeo2', parent=north,
                                   markdown='', entrance=WKTElement('POINT(5 45)', srid=4326))
            tstore.db.session.add_all([poolden, poolgeo2])
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)


def test_extract_gbuwh_short(test_app):
    add_uk(test_app)

    with test_app.app_context():
        uk = one(tstore.Place.query.filter_by(short_name='uk').all())
        feed = GbUwhFeed(
            source=GbUwhFeed.Source(name='GBUWH', icon='https://www.gbuwh.co.uk/logo.svg'),
            clubs=[
                GbUwhFeed.Club(
                    unique_id='c81e', name='Xarifa UWH', logo='https://www/xarifa-uwh.jpg',
                    region='North', website='https://foo.com',
                    sessions=[
                        GbUwhFeed.ClubSession(
                            day='tuesday', latitude=53.45, longitude=-2.11,
                            location_name='Denton', type='junior',
                            title='Xarifa Juniors',
                            start_time='19:00:00', end_time='20:00:00'),
                        GbUwhFeed.ClubSession(
                            day='thursday', latitude=53.47, longitude=-2.23,
                            location_name='Manch', type='adult',
                            title='Manchester session',
                            start_time='21:00:00', end_time='22:00:00')
                    ]),
            ]
        )
        fetch_timestamp = datetime(2022, 12, 25)
        scrape.extract_gbfeed(uk, feed, fetch_timestamp, scrape.ProblemAccumulator(logger=None))

    with test_app.app_context():
        all_pools: Mapping[str, tstore.Pool] = {p.name: p for p in tstore.Pool.query.all()}
        assert set(all_pools.keys()) == {'Denton', 'Manch'}
        assert tuple(one(all_pools['Denton'].entrance_shapely.coords)) == (-2.11, 53.45)

        all_clubs: Mapping[str, tstore.Club] = {c.name: c for c in tstore.Club.query.all()}
        assert set(all_clubs.keys()) == {'Xarifa UWH'}

        source: tstore.Source = one(tstore.Source.query.all())
        assert source.source_short_name == 'gbuwh-feed-clubs'
        assert source.sync_timestamp == fetch_timestamp

    # Check that running extract_gbfeed again updates tstore.Source.sync_timestamp
    with test_app.app_context():
        uk = one(tstore.Place.query.filter_by(short_name='uk').all())
        next_fetch_timestamp = datetime(2022, 12, 26)
        scrape.extract_gbfeed(uk, feed, next_fetch_timestamp, scrape.ProblemAccumulator(logger=None))
        updated_source: tstore.Source = one(tstore.Source.query.all())
        assert updated_source.id == source.id
        assert updated_source.sync_timestamp == next_fetch_timestamp

    with test_app.test_client() as c:
        with freeze_time(datetime.fromisoformat("2022-12-26T12:00:00")):
            response = c.get('/tourist/place/uknorth')
            response_text = response.get_data(as_text=True)
            source_text = re.search(r'provided by\s+GBUWH[^>]+>12 hours ago', response_text).group()
            assert source_text

            response = c.get('/tourist/')
            response_text = response.get_data(as_text=True)
            source_text = re.search(r'GBUWH updated [^>]+>United Kingdom[^>]+>\s+12 hours ago', response_text,
                                    flags=re.MULTILINE).group()
            assert source_text


def test_extract_gbuwh_wrong_region(test_app):
    add_uk(test_app)

    with test_app.app_context():
        uk = one(tstore.Place.query.filter_by(short_name='uk').all())
        feed = GbUwhFeed(
            source=GbUwhFeed.Source(name='GBUWH', icon='https://www.gbuwh.co.uk/logo.svg'),
            clubs=[
                GbUwhFeed.Club(
                    unique_id='c81e', name='Xarifa UWH', logo='https://www/xarifa-uwh.jpg',
                    region='London', website='https://foo.com',
                    sessions=[
                        GbUwhFeed.ClubSession(
                            day='thursday', latitude=53.4575, longitude=-2.114,
                            location_name='Denton Wellness Center', type='adult',
                            title='Manchester session',
                            start_time='21:00:00', end_time='22:00:00')
                    ]),
            ]
        )
        with pytest.raises(scrape.PoolRegionChanged):
            scrape.extract_gbfeed(uk, feed, datetime(2022, 12, 25),  scrape.ProblemAccumulator(logger=None))


def test_extract_gbuwh_change_location(test_app):
    add_uk(test_app, add_pools=False)

    with test_app.app_context():
        uk = one(tstore.Place.query.filter_by(short_name='uk').all())
        feed1 = GbUwhFeed(
            source=GbUwhFeed.Source(name='GBUWH', icon='https://www.gbuwh.co.uk/logo.svg'),
            clubs=[
                GbUwhFeed.Club(
                    unique_id='c81e', name='Xarifa UWH', logo='https://www/xarifa-uwh.jpg',
                    region='North', website='https://foo.com',
                    sessions=[
                        GbUwhFeed.ClubSession(
                            day='thursday', latitude=53.4575, longitude=-2.114,
                            location_name='Denton Wellness Center', type='adult',
                            title='Manchester session',
                            start_time='21:00:00', end_time='22:00:00')
                    ]),
            ]
        )
        scrape.extract_gbfeed(uk, feed1, datetime(2022, 12, 25),
                              scrape.ProblemAccumulator(logger=None))

    with test_app.app_context():
        uk = one(tstore.Place.query.filter_by(short_name='uk').all())
        # New feed with exact same data except the latitude changed to move location more than
        # 200m. This used to break the import because the old pool was deleted and new inserted
        # but sqlite raised an exception due to:
        # (sqlite3.IntegrityError) UNIQUE constraint failed: pool.short_name
        # Reach into feed1 and move the session location more than 200m. Kinda ugly but
        # https://github.com/python-attrs/attrs/issues/932 is tricky.
        feed2 = GbUwhFeed(
            source=feed1.source,
            clubs=[
                attr.evolve(feed1.clubs[0], sessions=[
                    attr.evolve(feed1.clubs[0].sessions[0], latitude=53.2575)])
            ])
        scrape.extract_gbfeed(uk, feed2, datetime(2022, 12, 25),
                              scrape.ProblemAccumulator(logger=None))


def test_extract_gbuwh_long(test_app):
    add_uk(test_app)

    jsonl_path = path_relative('url-fetch-20220720T110811-gbuwh.jsonl')
    json_line = one(open(jsonl_path).readlines())
    url_fetch = scrape.converter.structure(json.loads(json_line), sstore.UrlFetch)

    with test_app.app_context():
        uk_place = one(tstore.Place.query.filter_by(short_name='uk').all())

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.filterwarnings("always", category=scrape.ScraperWarning)
            scrape.parse_and_extract_gbfeed(uk_place, url_fetch, scrape.ProblemAccumulator(
                logger=None))

    expected_categories = [scrape.RegionNotFoundWarning, scrape.RegionNotFoundWarning,
                           scrape.RegionNotFoundWarning, scrape.RegionNotFoundWarning,
                           scrape.RegionNotFoundWarning, scrape.RegionNotFoundWarning,
                           scrape.RegionNotFoundWarning,
                           scrape.FeedContainsNonHttpsUrl,
                           scrape.FeedContainsNonHttpsUrl, scrape.FeedContainsNonHttpsUrl]
    found_categories = [w.category for w in caught_warnings]
    assert expected_categories == found_categories


def add_canada(test_app):
    some_region = WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                              '150.90 -34.42))', srid=4326)
    with test_app.app_context():
        world = tstore.Place(name='World', short_name='world', region=some_region, markdown='')
        ca = tstore.Place(name='Canada', short_name='ca', parent=world, region=some_region,
                          markdown='')
        cabc = tstore.Place(name='British Columbia', short_name='cabc', parent=ca,
                          region=some_region, markdown='')
        caon = tstore.Place(name='Ontario', short_name='caon', parent=ca,
                          region=some_region, markdown='')
        tstore.db.session.add_all([world, ca, cabc, caon])
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)


def test_load_and_comment(test_app):
    runner = ScrapeRunner(test_app)
    add_canada(test_app)
    jsonl_path = path_relative('entity-extract-20220309T021642.jsonl')
    runner.invoke_scrape(['load', f'--entity-extract-json-path={str(jsonl_path)}'])

    session = sstore.make_session(test_app.config['SCRAPER_DATABASE_URI'])

    @attr.frozen
    class CmpExtractComment:
        place_short_name: str
        created_timestamp: datetime
        has_place_comment_id: bool

        @staticmethod
        def make(e: sstore.EntityExtract) -> 'CmpExtractComment':
            return CmpExtractComment(e.place_short_name, e.created_timestamp, e.place_comment_id
                                     is not None)

    expected_extract_comments = {
        CmpExtractComment('cabc', datetime.fromisoformat('2022-03-09T00:00'), False),
        CmpExtractComment('caon', datetime.fromisoformat('2022-03-09T00:00'), True),
        CmpExtractComment('caon', datetime.fromisoformat('2022-03-10T00:00'), False),
    }
    assert expected_extract_comments == set(map(CmpExtractComment.make, session.query(
        sstore.EntityExtract).all()))

    runner.invoke_scrape(['comment'])

    with test_app.app_context():
        cabc = one(tstore.Place.query.filter_by(short_name='cabc').all())
        assert one(cabc.comments).content_markdown == "Now\n\nBC unchanged"
        caon = one(tstore.Place.query.filter_by(short_name='caon').all())
        assert one(caon.comments).content_markdown == "Was\n\nON original\n\nNow\n\nON updated"

    expected_extract_comments = {
        CmpExtractComment('cabc', datetime.fromisoformat('2022-03-09T00:00'), True),
        CmpExtractComment('caon', datetime.fromisoformat('2022-03-09T00:00'), True),
        CmpExtractComment('caon', datetime.fromisoformat('2022-03-10T00:00'), True),
    }
    assert expected_extract_comments == set(map(CmpExtractComment.make, session.query(
        sstore.EntityExtract).all()))


