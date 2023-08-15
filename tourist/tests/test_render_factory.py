from geoalchemy2 import WKTElement

import tourist
from tourist import render_factory
from tourist.models import tstore


polygon1 = WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                              '150.90 -34.42))', srid=4326)
point1 = WKTElement('POINT(150.90 -34)', srid=4326)


def test_geojson(test_app):
    with test_app.app_context():
        world = tstore.Place(name='World', short_name='world', region=polygon1, markdown='')
        country = tstore.Place(name='Country Name', short_name='cc', parent=world, region=polygon1,
                               markdown='')
        metro_with_pool = tstore.Place(
            name='Metro Name',
            short_name='metro_with_pool',
            parent=country,
            region=polygon1,
            markdown='',
        )
        metro_no_pool = tstore.Place(
            name='Metro No Pool',
            short_name='metro_no_pool',
            parent=country,
            region=polygon1,
            markdown='',
        )
        pool = tstore.Pool(name='Pool', short_name='poolnogeo', parent=metro_with_pool,
                           markdown='')
        poolgeo = tstore.Pool(name='Metro Pool', short_name='poolgeo', parent=metro_with_pool,
                           markdown='', entrance=point1)
        tstore.db.session.add_all([world, country, metro_with_pool, metro_no_pool, pool, poolgeo])
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)

    with test_app.app_context():
        collection = render_factory._build_geojson_feature_collection(tstore.Place.query.all(),
                                                                      tstore.Pool.query.all())

    titles = set(f['properties']['title'] for f in collection['features'])
    # Check that the collection contains the pool with geometry and metro without a pool.
    assert titles == {'Metro Pool', 'Metro No Pool'}

