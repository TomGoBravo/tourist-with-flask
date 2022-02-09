from pprint import pprint

import pytest
from geoalchemy2 import WKTElement
from pytest import approx
from tourist.scripts.sync import StaticSyncer
from tourist.tests.conftest import no_expire_on_commit
from tourist.tests.conftest import path_relative
from tourist.models import sqlalchemy, attrib
from tourist.scripts import sync
from shapely.geometry.geo import mapping
import json
from geoalchemy2.shape import to_shape
from freezegun import freeze_time


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
        assert {c.short_name for c in au._descendant_pools} == {'theforumaquaticscentrene',
                                                               'rydeaquaticcentrevictori',
                                                               'wollongonguniversityaqua',
                                                               'wollongonguniversityaqua2',
                                                                }

        world = sqlalchemy.Place.query.filter_by(short_name='world').first()
        assert len(world.descendant_places) == 5

    # Add a user
    with test_app.app_context():
        user = sqlalchemy.User(id=1, username='usernamefoo', email='testuser@domain.com', edit_granted=True)

        with no_expire_on_commit():
            sqlalchemy.db.session.add(user)
            sqlalchemy.db.session.commit()

        newsouthwales = sqlalchemy.Place.query.filter_by(short_name='newsouthwales').first()
        starfish = sqlalchemy.Club.query.filter_by(short_name='sydneystarfish').first()

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

        response = c.get(f'/tourist/edit/club/{starfish.id}')
        assert response.status_code == 302  # Without login

    with test_app.test_client(user=user) as c:
        response = c.get('/admin/place/edit/?id=3')
        assert response.status_code == 200

        response = c.post(f'/admin/place/edit/?id={newsouthwales.id}', data=dict(
            parent=newsouthwales.parent_id,
            name=newsouthwales.name + ' Changed',
            short_name=newsouthwales.short_name,
            region=json.dumps(mapping(to_shape(newsouthwales.region))),
        ))
        assert response.status_code == 302

        response = c.get(f'/tourist/edit/club/{starfish.id}')
        assert response.status_code == 200
        assert 'Provide at least ' in response.get_data(as_text=True)

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


def test_validate_place():
    with pytest.raises(ValueError, match='short_name'):
        sqlalchemy.Place(name='Foo', short_name=' bad_name').validate()

    with pytest.raises(ValueError, match='parent'):
        sqlalchemy.Place(name='Foo', short_name='shortie').validate()

    with pytest.raises(ValueError, match='Wiki'):
        sqlalchemy.Place(name='Foo', short_name='shortie', parent_id=2,
                         markdown='[[link]]').validate()

    sqlalchemy.Place(name='Foo', short_name='shortie', parent_id=2).validate()


def test_validate_club():
    with pytest.raises(ValueError, match='short_name'):
        sqlalchemy.Club(name='Foo', short_name=' bad_name').validate()

    with pytest.raises(ValueError, match='parent'):
        sqlalchemy.Club(name='Foo', short_name='shortie').validate()

    sqlalchemy.Club(name='Foo', short_name='shortie', parent_id=2, markdown='[[ou]]').validate()


def test_club_status_state():
    c = sqlalchemy.Club(name='Foo', short_name='shortie', parent_id=2, markdown='[[ou]]',
                        status_date='2021-01-15')
    with freeze_time("2022-01-15"):
        assert c.club_state == sqlalchemy.ClubState.CURRENT
    with freeze_time("2022-01-16"):
        assert c.club_state == sqlalchemy.ClubState.STALE

    c = sqlalchemy.Club(name='Foo', short_name='shortie', parent_id=2, markdown='[[ou]]',
                        status_date='')
    assert c.club_state == sqlalchemy.ClubState.STALE
    c.validate()


def add_some_entities(test_app):
    with test_app.app_context():
        world = sqlalchemy.Place(
            name='World',
            short_name='world',
            region=WKTElement('POLYGON(EMPTY)', srid=4326),
            id=1,
            markdown='',
        )
        country = sqlalchemy.Place(
            name='Country Name',
            short_name='cc',
            parent_id=1,
            region=WKTElement(
                'POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                '150.90 -34.42))', srid=4326),
            id=2,
            markdown='',
        )
        metro = sqlalchemy.Place(
            name='Metro Name',
            short_name='metro',
            parent_id=2,
            region=WKTElement(
                'POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                '150.90 -34.42))', srid=4326),
            id=3,
            markdown='',
        )
        club = sqlalchemy.Club(name='Foo Club', short_name='shortie', parent_id=3,
                               markdown='Foo Club plays at [[poolish]].',
                               id=1,
                               status_date='')
        pool = sqlalchemy.Pool(name='Metro Pool', short_name='poolish', parent_id=3,
                               markdown='Some palace', id=1)
        sqlalchemy.db.session.add_all([world, country, metro, club, pool])
        sqlalchemy.db.session.commit()


def add_and_return_edit_granted_user(test_app):
    with test_app.app_context():
        user_edit_granted = sqlalchemy.User(id=1, username='blah', email='testuser2@domain.com',
                                            edit_granted=True)
        with no_expire_on_commit():
            sqlalchemy.db.session.add_all([user_edit_granted])
            sqlalchemy.db.session.commit()
        return user_edit_granted


def test_club_without_status_date(test_app):
    add_some_entities(test_app)

    with test_app.app_context():
        assert not sqlalchemy.Club.query.filter_by(short_name='shortie').one().status_date

    with test_app.test_client() as c:
        response = c.get('/tourist/place/metro')
        assert response.status_code == 200
        assert 'Foo Club plays' in response.get_data(as_text=True)


def test_list(test_app):
    add_some_entities(test_app)

    with test_app.test_client() as c:
        response = c.get('/tourist/list')
        assert response.status_code == 200
        assert 'Country Name' in response.get_data(as_text=True)
        assert 'Metro Name' in response.get_data(as_text=True)
        assert 'Foo Club' in response.get_data(as_text=True)
        assert 'Metro Pool' in response.get_data(as_text=True)


def test_club_with_bad_pool_link(test_app):
    add_some_entities(test_app)

    with test_app.app_context():
        club = sqlalchemy.Club.query.filter_by(short_name='shortie').one()
        club.markdown += " * [[badpoollink]]\n"
        sqlalchemy.db.session.add(club)
        sqlalchemy.db.session.commit()

    with test_app.test_client() as c:
        response = c.get('/tourist/place/metro')
        assert response.status_code == 200
        assert 'Foo Club plays' in response.get_data(as_text=True)


def test_delete_club(test_app):
    add_some_entities(test_app)
    user = add_and_return_edit_granted_user(test_app)

    with test_app.test_client() as c:
        response = c.get('/tourist/delete/club/1')
        assert response.status_code == 302  # Without login

    with test_app.test_client(user=user) as c:
        response = c.get('/tourist/delete/club/1')
        assert response.status_code == 200
        assert 'Foo Club' in response.get_data(as_text=True)

        # Post without the `confirm` checkbox set.
        response = c.post(f'/tourist/delete/club/1', data=dict(csrf_token=c.csrf_token))
        assert response.status_code == 200

    # Check that delete didn't happen
    with test_app.app_context():
        assert sqlalchemy.Club.query.filter_by(short_name='shortie').count() == 1

    with test_app.test_client(user=user) as c:
        response = c.post(f'/tourist/delete/club/1', data=dict(confirm=True,
                                                               csrf_token=c.csrf_token))
        assert response.status_code == 302
        assert response.location.endswith('/tourist/place/metro')

    with test_app.app_context():
        assert sqlalchemy.Club.query.filter_by(short_name='shortie').count() == 0


def test_delete_place(test_app):
    add_some_entities(test_app)
    user = add_and_return_edit_granted_user(test_app)

    with test_app.test_client() as c:
        response = c.get('/tourist/delete/place/2')
        assert response.status_code == 302  # Without login

    with test_app.test_client(user=user) as c:
        response = c.get('/tourist/delete/place/2')
        assert response.status_code == 200
        assert 'can not be deleted' in response.get_data(as_text=True)

        response = c.post(f'/tourist/delete/place/3', data=dict(csrf_token=c.csrf_token))
        assert response.status_code == 200

    # Check that delete didn't happen
    with test_app.app_context():
        assert sqlalchemy.Club.query.filter_by(short_name='shortie').count() == 1

    with test_app.test_client(user=user) as c:
        response = c.post(f'/tourist/delete/place/3', data=dict(confirm=True,
                                                             csrf_token=c.csrf_token))
        assert response.status_code == 302
        assert response.location.endswith('/tourist/place/cc')

    with test_app.app_context():
        assert sqlalchemy.Club.query.filter_by(short_name='shortie').count() == 0


def test_delete_pool(test_app):
    add_some_entities(test_app)
    user = add_and_return_edit_granted_user(test_app)

    with test_app.test_client() as c:
        response = c.get('/tourist/delete/pool/1')
        assert response.status_code == 302  # Without login

    with test_app.test_client(user=user) as c:
        response = c.get('/tourist/delete/pool/1')
        assert response.status_code == 200
        assert 'can not be deleted' in response.get_data(as_text=True)

        with pytest.raises(ValueError, match="club_back_links"):
            c.post(f'/tourist/delete/pool/1', data=dict(confirm=True,
                                                                    csrf_token=c.csrf_token))

    # Check that delete didn't happen
    with test_app.app_context():
        assert sqlalchemy.Pool.query.filter_by(short_name='poolish').count() == 1
        club = sqlalchemy.Club.query.get(1)
        club.markdown = 'Club Foo has no pool'
        sqlalchemy.db.session.add(club)
        sqlalchemy.db.session.commit()

    with test_app.test_client(user=user) as c:
        response = c.post(f'/tourist/delete/pool/1', data=dict(confirm=True,
                                                               csrf_token=c.csrf_token))
        assert response.status_code == 302
        assert response.location.endswith('/tourist/place/metro')

    with test_app.app_context():
        assert sqlalchemy.Pool.query.filter_by(short_name='poolish').count() == 0


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


def test_page_not_found(test_app):
    with test_app.test_client() as c:
        response = c.get('/tourist/page/notfound')
        assert response.status_code == 404


def test_place_not_found(test_app):
    with test_app.test_client() as c:
        response = c.get('/tourist/place/notfound')
        assert response.status_code == 404


def test_uwht_redirect(test_app):
    add_some_entities(test_app)

    with test_app.test_client() as c:
        response = c.get('/uwht', follow_redirects=True)
        assert response.status_code == 200
        assert response.request.path == "/tourist/"

        response = c.get('/uwht?country=cc', follow_redirects=True)
        assert response.status_code == 200
        assert response.request.path == "/tourist/place/cc"


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

        # Remove 'id' from json_out before comparing because it wasn't part of the json when
        # testentities.jsonl was created.
        for j in json_out:
            del j['id']
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

