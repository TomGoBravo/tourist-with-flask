from geoalchemy2 import WKTElement
from pytest import approx
from tourist.scripts.sync import StaticSyncer
from tourist.tests.conftest import test_app, path_relative
from tourist.models import sqlalchemy, attrib
from tourist.scripts import sync
from shapely.geometry.geo import mapping
import json
from geoalchemy2.shape import to_shape

#import logging
#logging.basicConfig()
#logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)


def test_heavy(test_app):
    # Exercise the code that imports from Entity objects parsed from JSON.
    with test_app.app_context():
        importer = sync.Importer()
        importer.run(open(path_relative('testentities.jsonl')).readlines())

        pools = sqlalchemy.Pool.query.all()
        assert {p.name for p in pools} == {
            'The Forum Aquatics Centre',
            'Ryde Aquatic Centre',
            'Wollongong University Aquatics Center',
            'Wollongong University Aquatics Center 2'}

        pools = sqlalchemy.Pool.query.filter(
            sqlalchemy.Pool.entrance.isnot(None)).filter(
            sqlalchemy.Pool.entrance.ST_CoveredBy(
                WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,150.90 -34.42))'))).all()
        assert {p.name for p in pools} == {'Wollongong University Aquatics Center'}

        place = sqlalchemy.Place.query.filter(
            sqlalchemy.Place.region.isnot(None)).order_by(
            sqlalchemy.Place.region.ST_Centroid().ST_Distance(WKTElement('POINT(150.88 -34.41)'))).first()
        print(f'{repr(place.region)} ({type(place.region)})')
        assert 'Wollongong' == place.name

    with test_app.app_context():
        # Import again with no changes. This shouldn't do anything.
        importer = sync.Importer()
        importer.run(open(path_relative('testentities.jsonl')).readlines())
        assert importer.updater.to_add == []

    # test descendant_places
    with test_app.app_context():
        au = sqlalchemy.Place.query.filter_by(short_name='au').first()
        assert {c.short_name for c in au.descendant_places} == {
            'newsouthwales',
            'rydenewsouthwales',
            'newcastlenewsouthwales',
            'wollongongnewsouthwales'}
        assert {c.short_name for c in au.descendant_pools} == {'theforumaquaticscentrene',
                                                               'rydeaquaticcentrevictori',
                                                               'wollongonguniversityaqua',
                                                               'wollongonguniversityaqua2',
                                                               }

        world = sqlalchemy.Place.query.filter_by(short_name='world').first()
        assert len(world.descendant_places) == 5

    # Add a user
    with test_app.app_context():
        user = sqlalchemy.User(id=1, username='usernamefoo', email='testuser@domain.com', edit_granted=True)
        sqlalchemy.db.session.add(user)
        sqlalchemy.db.session.commit()

        newsouthwales = sqlalchemy.Place.query.filter_by(short_name='newsouthwales').first()

    with test_app.test_client() as c:
        response = c.get('/tourist/')
        assert response.status_code == 200

        response = c.get('/tourist/map')
        assert response.status_code == 200

        response = c.get('/tourist/about')
        assert response.status_code == 200

        response = c.get('/tourist/list')
        assert response.status_code == 200

        response = c.get('/tourist/data/pools.geojson')
        assert response.status_code == 200

        response = c.get('/admin/place/edit/?id=3')
        assert response.status_code == 302  # Without login

        # Login, from https://stackoverflow.com/a/16238537/341400
        with c.session_transaction() as sess:
            sess['user_id'] = 1
            sess['_fresh'] = True # https://flask-login.readthedocs.org/en/latest/#fresh-logins
        response = c.get('/admin/place/edit/?id=3')
        assert response.status_code == 200

        response = c.post(f'/admin/place/edit/?id={newsouthwales.id}', data=dict(
            parent=newsouthwales.parent_id,
            name=newsouthwales.name + ' Changed',
            short_name=newsouthwales.short_name,
            region=json.dumps(mapping(to_shape(newsouthwales.region))),
        ))
        assert response.status_code == 302

    with test_app.app_context():
        newnewsouthwales = sqlalchemy.Place.query.filter_by(short_name='newsouthwales').first()
        assert newnewsouthwales.name == 'New South Wales Changed'


def test_place_properties():
    place = sqlalchemy.Place(
        name='Test Name',
        short_name='short_name_test',
        region=WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,150.90 -34.42))')
    )
    assert place.area == approx(0.0012)
    assert place.area_text_scale == '\u25cf\u25cb\u25cb\u25cb\u25cb\u25cb\u25cb'

    place = sqlalchemy.Place(
        name='Test Name',
        short_name='short_name_test',
        region=WKTElement('POLYGON(EMPTY)')
    )
    assert place.area == 0
    assert place.area_text_scale == '\u25cb\u25cb\u25cb\u25cb\u25cb\u25cb\u25cb'


def test_static_sync_no_override():
    world = sqlalchemy.Place(
        name='World',
        short_name='world',
        region=WKTElement('POLYGON(EMPTY)'),
        id=1,
    )
    place = sqlalchemy.Place(
        name='Test Name',
        short_name='short_name_test',
        parent_id=1,
        region=WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,150.90 -34.42))'),
        id=2,
    )
    syncer = StaticSyncer()
    syncer.add_existing_place(world)
    syncer.add_existing_place(place)

    # Make an Entity without region, which is ignored. I don't want the region removed.
    place_update = attrib.Entity(
        type='place',
        name='Test Name',
        short_name=place.short_name,
        parent_short_name='world',
        region={},
        point={},
    )
    syncer.update_place(place_update)
    assert syncer.to_add == []


def test_import_export(test_app):
    with test_app.app_context():
        importer = sync.Importer()
        jsons_inputs = list(open(path_relative('testentities.jsonl')).readlines())
        importer.run(jsons_inputs)

        jsons_outputs = list(e.dump_as_jsons() for e in sync.get_sorted_entities())

        json_in = list(json.loads(j) for j in jsons_inputs)
        json_out = list(json.loads(j) for j in jsons_outputs)
        json_in.sort(key=lambda j: j['short_name'])
        json_out.sort(key=lambda j: j['short_name'])

        assert {j['short_name'] for j in json_in} == {j['short_name'] for j in json_out}

        assert json_in == json_out


def test_attrib_dump():
    input_str = (
        '{"type": "pool", "name": "Strand Pool", "short_name": "strandpoolbeachrdcapetow", '
        '"parent_short_name": "strandwesterncape", "point": {"type": "Point", '
        '"coordinates": [18.123456789, -33.123456789]}}')
    e = attrib.Entity.load_from_jsons(input_str)
    expected_output = (
        '{"type": "pool", "name": "Strand Pool", "short_name": "strandpoolbeachrdcapetow", '
        '"parent_short_name": "strandwesterncape", "point": {"type": "Point", '
        '"coordinates": [18.123457, -33.123457]}}')
    assert e.dump_as_jsons() == expected_output

    input_str = '{"type": "place", "name": "Harare", "short_name": "hararemashonaland", "parent_short_name": "mashonaland", "region": {"type": "Polygon", "coordinates": [[[31.220370270843453, -17.986784177289213], [31.220370270843453, -17.668649752640196], [30.886372588043265, -17.668649752640196], [30.886372588043265, -17.986784177289213], [31.220370270843453, -17.986784177289213]]]}}'
    e = attrib.Entity.load_from_jsons(input_str)
    expected_output = '{"type": "place", "name": "Harare", "short_name": "hararemashonaland", "parent_short_name": "mashonaland", "region": {"type": "Polygon", "coordinates": [[[31.22037, -17.986784], [31.22037, -17.66865], [30.886373, -17.66865], [30.886373, -17.986784], [31.22037, -17.986784]]]}}'
    assert e.dump_as_jsons() == expected_output

