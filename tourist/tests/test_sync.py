import pytest

from tourist.models import sqlalchemy
from tourist.scripts import sync


def test_get_sorted_entities(test_app):
    with test_app.app_context():
        place = sqlalchemy.Place(name='Some Town', short_name='town')
        pool = sqlalchemy.Pool(name='Pool Name', short_name='bad_name', parent=place)
        club = sqlalchemy.Club(name='Club Name', short_name='bad_name', parent=place)
        sqlalchemy.db.session.add_all([place, pool, club])
        sqlalchemy.db.session.commit()

        with pytest.raises(ValueError, match="Duplicates"):
            sync.get_sorted_entities()
