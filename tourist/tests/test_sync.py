import pytest
from geoalchemy2 import WKTElement

import tourist
from tourist.models import tstore
from tourist.scripts import sync


def test_get_sorted_entities_with_duplicate_short_name(test_app):
    with test_app.app_context():
        place = tstore.Place(name='World', short_name='world')
        pool = tstore.Pool(name='Pool Name', short_name='bad_name', parent=place)
        club = tstore.Club(name='Club Name', short_name='bad_name', parent=place)
        tstore.db.session.add_all([place, pool, club])
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)

        with pytest.raises(ValueError, match="Duplicates"):
            sync.get_sorted_entities()


def test_output_place(test_app):
    some_region = WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                             '150.90 -34.42))', srid=4326)
    with test_app.app_context():
        world = tstore.Place(name='World', short_name='world', markdown='')
        cc = tstore.Place(name='CC', short_name='cc', region=some_region, markdown='', parent=world)
        tstore.db.session.add_all([world, cc])
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)
        sync._output_place(place_short_name=['world', 'cc'])
