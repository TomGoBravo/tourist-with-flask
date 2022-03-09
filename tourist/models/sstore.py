from datetime import datetime
from typing import Optional
import attr
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import String
from sqlalchemy import Integer
from sqlalchemy import Table
from sqlalchemy import UnicodeText
import sqlalchemy
import sqlalchemy.orm


mapper_registry = sqlalchemy.orm.registry()


@mapper_registry.mapped
@attr.s(auto_attribs=True, kw_only=True)
class UrlFetch:
    __table__ = Table(
        "urlfetch",
        mapper_registry.metadata,
        Column("source_short_name", String, nullable=False),
        Column("url", String, primary_key=True),
        Column("created_timestamp", DateTime, primary_key=True),
        Column("unmodified_timestamp", DateTime, nullable=False),
        Column("response", UnicodeText, nullable=False),
        Column("extract_timestamp", DateTime, nullable=True),
    )
    source_short_name: str
    url: str
    created_timestamp: datetime = attr.ib(factory=lambda: datetime.utcnow())
    unmodified_timestamp: datetime = attr.ib(default=attr.Factory(
        lambda s: s.created_timestamp, takes_self=True))
    response: str

    # When extract is run `extract_timestamp` is set to `created_timestamp` of the EntityExtract
    extract_timestamp: Optional[datetime] = None


@mapper_registry.mapped
@attr.s(auto_attribs=True, kw_only=True, cmp=False)
class EntityExtract:
    __table__ = Table(
        "entityextract",
        mapper_registry.metadata,
        Column("source_short_name", String, nullable=False),
        Column("url", String, nullable=False, primary_key=True),
        Column("place_short_name", String, nullable=False, primary_key=True),
        Column("created_timestamp", DateTime, nullable=False, primary_key=True),
        Column("unmodified_timestamp", DateTime, nullable=False),
        Column("markdown_content", UnicodeText, nullable=False),
        Column("place_comment_id", Integer, nullable=True),
    )

    source_short_name: str
    url: str
    place_short_name: str
    created_timestamp: datetime = attr.ib(factory=datetime.utcnow)
    unmodified_timestamp: datetime = attr.ib(default=attr.Factory(
        lambda s: s.created_timestamp, takes_self=True))
    markdown_content: str
    place_comment_id: Optional[int] = None # Foreign key in the tstore database

    def _cmp_key(self):
        return self.url, self.place_short_name, self.created_timestamp

    def __eq__(self, other: 'EntityExtract'):
        return self._cmp_key() == other._cmp_key()

    def __lt__(self, other: 'EntityExtract'):
        return self._cmp_key() < other._cmp_key()


def make_session(engine_url: str):
    engine = sqlalchemy.create_engine(engine_url)
    mapper_registry.metadata.create_all(engine)
    session = sqlalchemy.orm.Session(engine)
    return session
