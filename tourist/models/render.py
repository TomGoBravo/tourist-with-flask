"""
Classes that are passed to jinja templates when rendering HTML. Use `render_factory` to make
instances of them from the sqlalchemy model objects. This additional layer of abstraction
decouples the templates from the database schema. Instances can be cached to render HTML without
loading heaps of objects from the database.
"""

from typing import Dict
from typing import List
from typing import Optional

import attrs


@attrs.frozen()
class Club:
    id: int
    name: str
    short_name: str
    markdown: str
    status_date: Optional[str]


@attrs.frozen()
class ClubShortNameName:
    name: str
    short_name: str


@attrs.frozen()
class Pool:
    id: int
    name: str
    short_name: str
    markdown: str
    club_back_links: List[ClubShortNameName]
    maps_point_query: str


@attrs.frozen()
class Bounds:
    north: float
    south: float
    west: float
    east: float


@attrs.frozen()
class ChildPlace:
    path: str
    name: str


@attrs.frozen()
class Place:
    id: int
    name: str
    short_name: str
    markdown: str
    geojson_children_collection: Dict
    child_clubs: List[Club]
    child_pools: List[Pool]
    bounds: Optional[Bounds]
    child_places: List[ChildPlace]
    parents: List[ChildPlace]


@attrs.frozen()
class PlaceRecursiveNames:
    id: int
    name: str
    path: str
    area: int
    child_clubs: List["PlaceRecursiveNames.Club"]
    child_pools: List["PlaceRecursiveNames.Pool"]
    child_places: List["PlaceRecursiveNames"]

    @attrs.frozen()
    class Club:
        name: str

    @attrs.frozen()
    class Pool:
        name: str
