import json

from geoalchemy2.shape import to_shape
from shapely.geometry import mapping

from tourist.models import sqlalchemy
from tourist.scripts import sync
from tourist.tests.conftest import test_app, path_relative


def test_heavy(test_app):
    with test_app.app_context():
        world = sqlalchemy.Place(id=1, name='World', short_name='world', markdown='')
        au = sqlalchemy.Place(id=2, name='Australia', short_name='au', parent_id=1, markdown='')

        user_plain = sqlalchemy.User(id=1, username='usernamefoo', email='testuser1@domain.com')
        user_edit_granted = sqlalchemy.User(id=2, username='blah', email='testuser2@domain.com', edit_granted=True)

        sqlalchemy.db.session.add_all([world, au, user_plain, user_edit_granted])
        sqlalchemy.db.session.commit()

    with test_app.test_client() as c:
        response = c.get('/tourist/')
        assert b'Sign in' in response.data
        assert b'Edit' not in response.data

        response = c.get('/tourist/place/au')
        assert b'Sign in' in response.data
        assert b'Edit' not in response.data

        response = c.get('/admin/place/edit/?id=3')
        assert response.status_code == 302  # Without login

        # Login. This user isn't authorized to /admin
        with c.session_transaction() as sess:
            sess['user_id'] = 1
            sess['_fresh'] = True # https://flask-login.readthedocs.org/en/latest/#fresh-logins
        response = c.get('/tourist/')
        assert b'Sign out' in response.data

        response = c.get('/tourist/place/au')
        assert b'Sign out' in response.data

        response = c.get('/admin/place/edit/?id=3')
        assert response.status_code == 403

        au = sqlalchemy.Place.query.filter_by(short_name='au').one()
        response = c.post(f'/admin/place/edit/?id={au.id}', data=dict(
            parent=au.parent_id,
            name=au.name + ' Changed',
            short_name=au.short_name,
        ))
        assert response.status_code == 403

    with test_app.app_context():
        new_au = sqlalchemy.Place.query.filter_by(short_name='au').first()
        assert new_au.name == 'Australia'

    with test_app.test_client() as c:
        # Login. This user is authorized to /admin
        with c.session_transaction() as sess:
            sess['user_id'] = 2
            sess['_fresh'] = True
        response = c.get('/tourist/')
        assert b'Sign out' in response.data

        response = c.get('/tourist/place/au')
        assert b'Sign out' in response.data

        with c.session_transaction() as sess:
            assert sess['user_id'] == 2

        response = c.get(f'/admin/place/edit/?id={au.id}')
        assert response.status_code == 200
        assert b'Sign out' in response.data

        response = c.post(f'/admin/place/edit/?id={au.id}', data=dict(
            parent=au.parent_id,
            name=au.name + ' Changed',
            short_name=au.short_name,
        ))
        assert response.status_code == 302

    with test_app.app_context():
        new_au = sqlalchemy.Place.query.filter_by(short_name='au').one()
        assert new_au.name == 'Australia Changed'
