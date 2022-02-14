import pytest

from tourist.models import tstore
from tourist.scripts import sync


def test_get_sorted_entities_with_duplicate_short_name(test_app):
    with test_app.app_context():
        place = tstore.Place(name='World', short_name='world')
        pool = tstore.Pool(name='Pool Name', short_name='bad_name', parent=place)
        club = tstore.Club(name='Club Name', short_name='bad_name', parent=place)
        tstore.db.session.add_all([place, pool, club])
        tstore.db.session.commit()

        with pytest.raises(ValueError, match="Duplicates"):
            sync.get_sorted_entities()
