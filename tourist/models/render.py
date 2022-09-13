"""
Classes that are passed to jinja templates when rendering HTML. Use `render_factory` to make
instances of them from the sqlalchemy model objects. This additional layer of abstraction
decouples the templates from the database schema. Instances can be cached to render HTML without
loading heaps of objects from the database.
"""
import datetime
from enum import Enum
from enum import unique

from typing import Dict
from typing import ForwardRef
from typing import List
from typing import Optional

import attrs
import cattrs


@unique
class ClubState(Enum):
    CURRENT = '\N{HEAVY CHECK MARK}'
    STALE = '?'


@attrs.frozen()
class Club:
    id: int
    name: str
    short_name: str
    markdown: str
    status_date: Optional[str]
    logo_url: Optional[str] = None
    source_name: Optional[str] = None
    source_logo_url: Optional[str] = None

    @property
    def club_state(self) -> ClubState:
        if not self.status_date:
            return ClubState.STALE

        d = datetime.datetime.fromisoformat(self.status_date)
        if (datetime.datetime.now() - d) > datetime.timedelta(days=365):
            return ClubState.STALE
        else:
            return ClubState.CURRENT


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
class PlaceComment:
    id: int
    timestamp: datetime.datetime
    source: str
    content: Optional[str] = None
    content_markdown: Optional[str] = None


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
    comments: List[PlaceComment] = attrs.field(factory=list)


@attrs.frozen()
class PlaceRecursiveNames:
    id: int
    name: str
    path: str
    area: float
    child_clubs: "List[PlaceRecursiveNames.Club]"
    child_pools: "List[PlaceRecursiveNames.Pool]"
    child_pools_without_club_back_links: "List[PlaceRecursiveNames.Pool]"
    child_places: "List[PlaceRecursiveNames]"
    comment_count: int = attrs.field(default=0)

    @attrs.frozen()
    class Club:
        name: str

    @attrs.frozen()
    class Pool:
        name: str


cattrs.register_structure_hook(datetime.datetime, lambda d, t: datetime.datetime.fromisoformat(d))
cattrs.register_unstructure_hook(datetime.datetime, lambda d: d.isoformat())
