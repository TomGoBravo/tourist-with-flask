import pathlib
import shutil
from datetime import datetime
from typing import Sequence

import attr
import click.testing
import responses
from freezegun import freeze_time
from geoalchemy2 import WKTElement
from more_itertools import one

import tourist.config
from tourist.models import sstore
from tourist.models import tstore
from tourist.scripts import scrape
from tourist.tests.conftest import path_relative


@responses.activate
def test_fetch(monkeypatch):
    source0 = scrape.sources[0]
    monkeypatch.setattr(scrape, 'sources', [source0])
    responses.add(responses.GET, body="hello world", url=source0.url)

    session = sstore.make_session("sqlite://")

    # First fetch
    created_datetime = datetime.fromisoformat("2022-03-08T21:04:00")
    with freeze_time(created_datetime):
        scrape.fetch(session)

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
        scrape.fetch(session)
    fetch = one(session.query(sstore.UrlFetch).all())
    expected_fetch = attr.evolve(expected_fetch, unmodified_timestamp=fetch_again_datetime)
    assert fetch == expected_fetch

    # Fetch again with modified response
    responses.add(responses.GET, body="hello world, again", url=source0.url)
    fetch_updated_datetime = datetime.fromisoformat("2022-03-08T23:04:00")
    with freeze_time(fetch_updated_datetime):
        scrape.fetch(session)
    expected_fetch_again = attr.evolve(expected_fetch, created_timestamp=fetch_updated_datetime,
                                       unmodified_timestamp=fetch_updated_datetime,
                                       response="hello world, again")
    assert [expected_fetch, expected_fetch_again] == list(session.query(
        sstore.UrlFetch).order_by(sstore.UrlFetch.created_timestamp).all())


@attr.define
class ScrapeRunner():
    data_dir: pathlib.Path
    cli_runner: click.testing.CliRunner = attr.ib(factory=lambda: click.testing.CliRunner(
        mix_stderr=False))

    def invoke_scrape(self, args: Sequence[str]) -> click.testing.Result:
        env = {'FLASK_ENV': 'development', 'FLASK_APP': 'tourist', 'DATA_DIR': str(self.data_dir)}
        result = self.cli_runner.invoke(scrape.scrape_cli, args,
                                        env=env, catch_exceptions=False)
        print(result.stdout)
        print(result.stderr)
        assert result.exit_code == 0
        return result


def test_load_and_extract(test_app):
    runner = ScrapeRunner(test_app.config['DATA_DIR'])
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


def test_load_and_comment(test_app):
    runner = ScrapeRunner(test_app.config['DATA_DIR'])
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


