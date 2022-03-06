import datetime
import re
from typing import Callable
from typing import Iterable
from typing import List
from typing import Tuple

import bs4
import sqlalchemy
import sqlalchemy.orm

import requests
from flask.cli import AppGroup
from more_itertools import one

from tourist.models import sstore
import attrs

from tourist.models import tstore

scrape_cli = AppGroup('scrape')


@attrs.frozen()
class Source:
    short_name: str
    url: str
    place_short_name: str
    extractor: Callable[[tstore.Place, sstore.UrlFetch], List[sstore.FetchedEntity]]


@scrape_cli.command('fetch')
def fetch():
    session = make_session()
    for source in sources:
        response = requests.get(source.url)
        if response.status_code != 200:
            raise ValueError(f"Bad Response {source}, {response}")
        prev_fetch = session.query(sstore.UrlFetch).filter_by(url=source.url).first()  # XXX order by created_
        if prev_fetch and prev_fetch.response == response:
            prev_fetch.unmodified_timestamp = datetime.datetime.utcnow()
        else:
            new_fetch = sstore.UrlFetch(source_short_name=source.short_name, url=source.url,
                                     response=response.text)
            session.add(new_fetch)
    session.commit()


def make_session():
    engine_url = 'sqlite:////home/thecap/code/tourist-with-flask/scraper.db'
    engine = sqlalchemy.create_engine(engine_url)
    sstore.Base.metadata.create_all(engine)
    session = sqlalchemy.orm.Session(engine)
    return session


@attrs.frozen()
class PlaceSearcher:
    parent_place: tstore.Place

    def _descendant_names(self, place: tstore.Place) -> Iterable[Tuple[str, tstore.Place]]:
        for child in place.child_places:
            yield (child.name, child)
            yield from self._descendant_names(child)

    def find_by_name(self, name: str) -> tstore.Place:
        descendant_names_pairs = list(self._descendant_names(self.parent_place))
        descendant_names = dict(descendant_names_pairs)
        if len(descendant_names_pairs) != len(descendant_names):
            raise ValueError(f"Duplicate name in {self.parent_place.short_name}?")
        return descendant_names[name]

    def find_by_short_name(self, short_name: str) -> tstore.Place:
        return tstore.db.session.query(tstore.Place).filter_by(short_name=short_name).one()


def extract_cuga(parent_place: tstore.Place, fetch: sstore.UrlFetch) -> List[sstore.FetchedEntity]:
    place_searcher = PlaceSearcher(parent_place)
    results = []
    soup = bs4.BeautifulSoup(fetch.response, 'html.parser')
    parts = soup.find_all('div', attrs={'data-vc-content': '.vc_tta-panel-body'})
    for part in parts:
        name = one(part.find_all('span', class_='vc_tta-title-text')).string
        content = part.find_all('div', class_='vc_tta-panel-body')
        if len(content) != 1:
            raise ValueError(f"Expected one panel body")
        content = content[0]
        p_blocks = content.find_all('p')
        if len(p_blocks) == 1 and re.search(r'no.*clubs here', p_blocks[0].text):
            continue
        try:
            tstore_place = place_searcher.find_by_name(name)
        except KeyError:
            print(f"Can't find place {name}")  # Change to some kind of warning
            continue
        entity = sstore.FetchedEntity(
            source_short_name=fetch.source_short_name,
            url=fetch.url,
            place_short_name=tstore_place.short_name,
            html_content=str(content),
        )
        results.append(entity)
    return results


def extract_sauwhf(parent_place: tstore.Place, fetch: sstore.UrlFetch) -> List[
    sstore.FetchedEntity]:
    return []


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
    session = make_session()
    world_place = tstore.Place.query.filter_by(short_name='world').one()
    world_place_searcher = PlaceSearcher(world_place)
    for fetch in session.query(sstore.UrlFetch).all():
        source = source_by_short_name[fetch.source_short_name]
        parent_place = world_place_searcher.find_by_short_name(source.place_short_name)
        entities = source.extractor(parent_place, fetch)
        # XXX Update unmodified when html isn't changed like in fetch
        session.add_all(entities)
    session.commit()


@scrape_cli.command('comment')
def comment_command():
    session = make_session()
    world_place = tstore.Place.query.filter_by(short_name='world').one()
    world_place_searcher = PlaceSearcher(world_place)
    comments = []
    for extract in session.query(sstore.FetchedEntity).filter_by(place_comment_id=None):
        place = world_place_searcher.find_by_short_name(extract.place_short_name)
        comment = tstore.PlaceComment(
            source=f"Fetched from {extract.url}",
            content=extract.html_content,
            place=place,
        )
        comments.append(comment)
    tstore.db.session.add_all(comments)
    tstore.db.session.commit()

