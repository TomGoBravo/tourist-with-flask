import datetime
from pprint import pprint

import pytest
from geoalchemy2 import WKTElement
from pytest import approx

import tourist.models.render
from tourist import render_factory
from tourist.scripts.sync import StaticSyncer
from tourist.tests.conftest import no_expire_on_commit
from tourist.tests.conftest import path_relative
from tourist.models import tstore, attrib
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

        pools = tstore.Pool.query.all()
        assert {p.name for p in pools} == {
            'The Forum Aquatics Centre',
            'Ryde Aquatic Centre',
            'Wollongong University Aquatics Center',
            'Wollongong University Aquatics Center 2'}

        pools = tstore.Pool.query.filter(
            tstore.Pool.entrance.isnot(None)).filter(
            tstore.Pool.entrance.ST_CoveredBy(
                WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,150.90 -34.42))'))).all()
        assert {p.name for p in pools} == {'Wollongong University Aquatics Center'}

        place = tstore.Place.query.filter(
            tstore.Place.region.isnot(None)).order_by(
            tstore.Place.region.ST_Centroid().ST_Distance(WKTElement('POINT(150.88 -34.41)'))).first()
        print(f'{repr(place.region)} ({type(place.region)})')
        assert 'Wollongong' == place.name

    with test_app.app_context():
        # Import again with no changes. This shouldn't do anything.
        importer = sync.Importer()
        importer.run(open(path_relative('testentities.jsonl')).readlines())
        assert importer.updater.to_add == []

    # test _geojson_features
    with test_app.app_context():
        au = tstore.Place.query.filter_by(short_name='au').first()
        assert {f['properties']['title'] for f in au.children_or_center_geojson_features} == {
            "The Forum Aquatics Centre", "Ryde Aquatic Centre",
            "Wollongong University Aquatics Center", "Wollongong University Aquatics Center 2"
        }

        world = tstore.Place.query.filter_by(short_name='world').first()
        assert len(world.children_or_center_geojson_features) == len(au.children_or_center_geojson_features)

    # Add a user
    with test_app.app_context():
        user = tstore.User(id=1, username='usernamefoo', email='testuser@domain.com', edit_granted=True)

        with no_expire_on_commit():
            tstore.db.session.add(user)
            tstore.db.session.commit()

        newsouthwales = tstore.Place.query.filter_by(short_name='newsouthwales').first()
        starfish = tstore.Club.query.filter_by(short_name='sydneystarfish').first()

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
        newnewsouthwales = tstore.Place.query.filter_by(short_name='newsouthwales').first()
        assert newnewsouthwales.name == 'New South Wales Changed'


def test_place_properties():
    place = tstore.Place(
        name='Test Name',
        short_name='short_name_test',
        region=WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,150.90 -34.42))')
    )
    assert place.area == approx(0.0012)

    place = tstore.Place(
        name='Test Name',
        short_name='short_name_test',
        region=WKTElement('POLYGON(EMPTY)')
    )
    assert place.area == 0


def test_validate_place():
    with pytest.raises(ValueError, match='short_name'):
        tstore.Place(name='Foo', short_name=' bad_name').validate()

    with pytest.raises(ValueError, match='parent'):
        tstore.Place(name='Foo', short_name='shortie').validate()

    with pytest.raises(ValueError, match='Wiki'):
        tstore.Place(name='Foo', short_name='shortie', parent_id=2,
                         markdown='[[link]]').validate()

    tstore.Place(name='Foo', short_name='shortie', parent_id=2).validate()


def test_validate_club():
    with pytest.raises(ValueError, match='short_name'):
        tstore.Club(name='Foo', short_name=' bad_name').validate()

    with pytest.raises(ValueError, match='parent'):
        tstore.Club(name='Foo', short_name='shortie').validate()

    tstore.Club(name='Foo', short_name='shortie', parent_id=2, markdown='[[ou]]').validate()


def test_club_status_state():
    c = tourist.models.render.Club(
        id=1, name='Foo', short_name='shortie', markdown='[[ou]]', status_date='2021-01-15',
    )
    with freeze_time("2022-01-15"):
        assert c.club_state == tourist.models.render.ClubState.CURRENT
    with freeze_time("2022-01-16"):
        assert c.club_state == tourist.models.render.ClubState.STALE

    c = tourist.models.render.Club(
        id=1, name='Foo', short_name='shortie', markdown='[[ou]]', status_date='',
    )
    assert c.club_state == tourist.models.render.ClubState.STALE


def add_some_entities(test_app):
    with test_app.app_context():
        world = tstore.Place(
            name='World',
            short_name='world',
            region=WKTElement('POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                '150.90 -34.42))', srid=4326),
            id=1,
            markdown='',
        )
        country = tstore.Place(
            name='Country Name',
            short_name='cc',
            parent_id=1,
            region=WKTElement(
                'POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                '150.90 -34.42))', srid=4326),
            id=2,
            markdown='',
        )
        metro = tstore.Place(
            name='Metro Name',
            short_name='metro',
            parent_id=2,
            region=WKTElement(
                'POLYGON((150.90 -34.42,150.90 -34.39,150.86 -34.39,150.86 -34.42,'
                '150.90 -34.42))', srid=4326),
            id=3,
            markdown='',
        )
        club = tstore.Club(name='Foo Club', short_name='shortie', parent_id=3,
                               markdown='Foo Club plays at [[poolish]].',
                               id=1,
                               status_date='')
        pool = tstore.Pool(name='Metro Pool', short_name='poolish', parent_id=3,
                               markdown='Some palace', id=1)
        tstore.db.session.add_all([world, country, metro, club, pool])
        tstore.db.session.commit()


def add_and_return_edit_granted_user(test_app):
    with test_app.app_context():
        user_edit_granted = tstore.User(id=1, username='blah', email='testuser2@domain.com',
                                            edit_granted=True)
        with no_expire_on_commit():
            tstore.db.session.add_all([user_edit_granted])
            tstore.db.session.commit()
        return user_edit_granted


def test_club_without_status_date(test_app):
    add_some_entities(test_app)

    with test_app.app_context():
        assert not tstore.Club.query.filter_by(short_name='shortie').one().status_date

    with test_app.test_client() as c:
        response = c.get('/tourist/place/metro')
        assert response.status_code == 200
        # Check club markdown
        assert 'Foo Club plays' in response.get_data(as_text=True)
        # Check link from pool back to club
        assert 'Foo Club</a> practices here' in response.get_data(as_text=True)


def test_list(test_app):
    add_some_entities(test_app)

    with test_app.test_client() as c:
        response = c.get('/tourist/list')
        assert response.status_code == 200
        assert 'Country Name' in response.get_data(as_text=True)
        assert 'Metro Name' in response.get_data(as_text=True)
        assert 'Foo Club' in response.get_data(as_text=True)
        assert 'Metro Pool' in response.get_data(as_text=True)

    with test_app.app_context():
        metro = tstore.Place(
            name='Metro Name',
            short_name='metronogeom',
            parent_id=2,
            region=None,
            markdown='',
        )
        tstore.db.session.add_all([metro])
        tstore.db.session.commit()

    with test_app.test_client() as c:
        response = c.get('/tourist/list')
        assert response.status_code == 200
        assert 'Metro Name</a> (please add region)' in response.get_data(as_text=True)


def test_club_with_bad_pool_link(test_app):
    add_some_entities(test_app)

    with test_app.app_context():
        club = tstore.Club.query.filter_by(short_name='shortie').one()
        club.markdown += " * [[badpoollink]]\n"
        tstore.db.session.add(club)
        tstore.db.session.commit()

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
        assert tstore.Club.query.filter_by(short_name='shortie').count() == 1

    with test_app.test_client(user=user) as c:
        response = c.post(f'/tourist/delete/club/1', data=dict(confirm=True,
                                                               csrf_token=c.csrf_token))
        assert response.status_code == 302
        assert response.location.endswith('/tourist/place/metro')

    with test_app.app_context():
        assert tstore.Club.query.filter_by(short_name='shortie').count() == 0


def test_delete_place(test_app):
    add_some_entities(test_app)
    user = add_and_return_edit_granted_user(test_app)

    with test_app.test_client() as c:
        response = c.get('/tourist/delete/place/2')
        assert response.status_code == 302  # Without login

        response = c.get('/tourist/place/metro')
        assert response.status_code == 200

        response = c.get('/tourist/place/cc')
        assert "Metro" in response.get_data(as_text=True)

    with test_app.test_client(user=user) as c:
        response = c.get('/tourist/delete/place/2')
        assert response.status_code == 200
        assert 'can not be deleted' in response.get_data(as_text=True)

        response = c.post(f'/tourist/delete/place/3', data=dict(csrf_token=c.csrf_token))
        assert response.status_code == 200

    # Check that delete didn't happen because `confirm` was not set
    with test_app.app_context():
        assert tstore.Club.query.filter_by(short_name='shortie').count() == 1

    with test_app.test_client(user=user) as c:
        response = c.post(f'/tourist/delete/place/3', data=dict(confirm=True,
                                                             csrf_token=c.csrf_token))
        assert response.status_code == 302
        assert response.location.endswith('/tourist/place/cc')

    with test_app.app_context():
        assert tstore.Club.query.filter_by(short_name='shortie').count() == 0

    with test_app.test_client() as c:
        response = c.get('/tourist/place/metro')
        assert response.status_code == 404

        response = c.get('/tourist/place/cc')
        assert "Metro" not in response.get_data(as_text=True)


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
        assert tstore.Pool.query.filter_by(short_name='poolish').count() == 1
        club = tstore.Club.query.get(1)
        club.markdown = 'Club Foo has no pool'
        tstore.db.session.add(club)
        tstore.db.session.commit()

    with test_app.test_client(user=user) as c:
        response = c.post(f'/tourist/delete/pool/1', data=dict(confirm=True,
                                                               csrf_token=c.csrf_token))
        assert response.status_code == 302
        assert response.location.endswith('/tourist/place/metro')

    with test_app.app_context():
        assert tstore.Pool.query.filter_by(short_name='poolish').count() == 0


def test_static_sync_no_override():
    world = tstore.Place(
        name='World',
        short_name='world',
        region=WKTElement('POLYGON(EMPTY)'),
        id=1,
    )
    place = tstore.Place(
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


def test_add_delete_place_comment(test_app):
    add_some_entities(test_app)
    user = add_and_return_edit_granted_user(test_app)
    place_id = 3

    with test_app.test_client() as c:
        response = c.get('/tourist/place/metro')
        assert 'There are comments' not in response.get_data(as_text=True)

        response = c.post(f'/tourist/add/place_comment/{place_id}',
                          data=dict(content="test content"))
        assert response.status_code == 302
        assert response.location.endswith('/tourist/place/metro')

        response = c.get('/tourist/place/metro')
        assert 'There are comments' in response.get_data(as_text=True)

    with test_app.test_client(user=user) as c:
        response = c.get('/tourist/place/metro')
        assert 'test content' in response.get_data(as_text=True)

        response = c.get('/tourist/comments')
        assert 'test content' in response.get_data(as_text=True)
        assert 'Metro Name' in response.get_data(as_text=True)

    # Check that deleting the place deletes the comment.
    with test_app.app_context():
        place = tstore.Place.query.get(place_id)
        assert len(place.comments) == 1
        assert place.comments[0].source == "Web visitor at 127.0.0.1"
        assert 0 <= (datetime.datetime.utcnow() - place.comments[0].timestamp).total_seconds() <= 10
        comment_id = place.comments[0].id
        assert tstore.PlaceComment.query.get(comment_id)
        # child_pools and child_clubs are not deleted by cascade. Delete them explicitly.
        tstore.db.session.delete(place.child_pools[0])
        tstore.db.session.delete(place.child_clubs[0])
        tstore.db.session.delete(place)
        tstore.db.session.commit()
        # Check that comment_id was implicitly deleted by cascade.
        assert tstore.PlaceComment.query.get(comment_id) is None
