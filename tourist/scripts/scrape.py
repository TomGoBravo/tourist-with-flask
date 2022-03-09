import json
from collections import defaultdict
from datetime import datetime
import re
from typing import Callable
from typing import Iterable
from typing import List
from typing import Mapping
from typing import Optional
from typing import Tuple

import attr
import bs4
import cattrs
import click
import flask
import sqlalchemy.orm

import requests
from flask.cli import AppGroup
from markdownify import MarkdownConverter
from more_itertools import one
from sortedcontainers import SortedList

from tourist.models import sstore
import attrs

from tourist.models import tstore
import tourist.config

scrape_cli = AppGroup('scrape')


@attrs.frozen()
class Source:
    short_name: str
    url: str
    place_short_name: str
    extractor: Callable[[tstore.Place, sstore.UrlFetch], List[sstore.EntityExtract]]


def fetch(session: sqlalchemy.orm.Session):
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
    fetch(make_session_from_flask_config())


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

    def find_by_name(self, name: str) -> tstore.Place:
        descendant_names_pairs = list(self._descendant_names(self.parent_place))
        descendant_names = dict(descendant_names_pairs)
        if len(descendant_names_pairs) != len(descendant_names):
            raise ValueError(f"Duplicate name in {self.parent_place.short_name}?")
        return descendant_names[name.lower()]

    def find_by_short_name(self, short_name: str) -> tstore.Place:
        return tstore.db.session.query(tstore.Place).filter_by(short_name=short_name).one()


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
        try:
            tstore_place = place_searcher.find_by_name(name)
        except KeyError:
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
        try:
            tstore_place = place_searcher.find_by_name(province_name)
        except KeyError:
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


sources = [
    Source("cuga-uwh", "http://cuga.org/en/where-and-when-uwh/", "ca", extract_cuga),
    Source("sauwhf", "https://sauwhf.co.za/clubs/", "za", extract_sauwhf),
]

source_by_short_name = {source.short_name: source for source in sources}
# sources could be an enum, but I'd rather have it in a representation that is closer to being
# stored as data instead of in the source code.
assert len(sources) == len(source_by_short_name)


@scrape_cli.command('extract')
def extract_command():
    session = make_session_from_flask_config()
    world_place = tstore.Place.query.filter_by(short_name='world').one()
    world_place_searcher = PlaceSearcher(world_place)
    utc_now = datetime.utcnow()
    for fetch in session.query(sstore.UrlFetch).filter_by(extract_timestamp=None).all():
        source = source_by_short_name[fetch.source_short_name]
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

