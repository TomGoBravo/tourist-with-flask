from datetime import datetime

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import String
from sqlalchemy import Integer
from sqlalchemy import UnicodeText
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session


Base = declarative_base()


class UrlFetch(Base):
    __tablename__ = "urlfetch"

    source_short_name = Column(String, nullable=False)
    url = Column(String, nullable=False, primary_key=True)
    created_timestamp = Column(DateTime, nullable=False, primary_key=True, default=datetime.now())
    unmodified_timestamp = Column(DateTime, nullable=False, default=datetime.now())
    response = Column(UnicodeText, nullable=False)


class FetchedEntity(Base):
    __tablename__ = "fetchedentity"

    source_short_name = Column(String, nullable=False)
    url = Column(String, nullable=False, primary_key=True)
    place_short_name = Column(String, nullable=False, primary_key=True)
    created_timestamp = Column(DateTime, nullable=False, primary_key=True, default=datetime.now())
    unmodified_timestamp = Column(DateTime, nullable=False, default=datetime.now())
    html_content = Column(UnicodeText, nullable=False)
    markdown_content = Column(UnicodeText, nullable=False)
    place_comment_id = Column(Integer, nullable=True)  # Foreign key in the tstore database