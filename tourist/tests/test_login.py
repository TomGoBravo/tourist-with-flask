from tourist.models import tstore
from tourist.tests.conftest import no_expire_on_commit


def test_heavy(test_app):
    with test_app.app_context():
        world = tstore.Place(id=1, name='World', short_name='world', markdown='')
        au = tstore.Place(id=2, name='Australia', short_name='au', parent_id=1, markdown='')

        user_plain = tstore.User(id=1, username='usernamefoo', email='testuser1@domain.com')
        user_edit_granted = tstore.User(id=2, username='blah', email='testuser2@domain.com', edit_granted=True)

        with no_expire_on_commit():
            tstore.db.session.add_all([world, au, user_plain, user_edit_granted])
            tstore.db.session.commit()

    with test_app.test_client() as c:
        response = c.get('/tourist/')
        assert b'Sign in' in response.data
        assert b'Edit' not in response.data

        response = c.get('/tourist/place/au')
        assert b'Sign in' in response.data
        assert b'Edit' not in response.data

        response = c.get('/admin/place/edit/?id=3')
        assert response.status_code == 302  # Without login

        response = c.get('/tourist/comments')
        assert response.status_code == 302  # Without login, redirects

    # Login. This user isn't authorized to /admin
    with test_app.test_client(user=user_plain) as c:
        response = c.get('/tourist/')
        assert b'Sign out' in response.data

        response = c.get('/tourist/place/au')
        assert b'Sign out' in response.data

        response = c.get('/admin/place/edit/?id=3')
        assert response.status_code == 403

        au = tstore.Place.query.filter_by(short_name='au').one()
        response = c.post(f'/admin/place/edit/?id={au.id}', data=dict(
            parent=au.parent_id,
            name=au.name + ' Changed',
            short_name=au.short_name,
        ))
        assert response.status_code == 403

        response = c.get('/admin/comment/')
        assert response.status_code == 403

        response = c.get('/tourist/comments')
        assert response.status_code == 403

    with test_app.app_context():
        new_au = tstore.Place.query.filter_by(short_name='au').first()
        assert new_au.name == 'Australia'

    # Login. This user is authorized to /admin
    with test_app.test_client(user=user_edit_granted) as c:
        response = c.get('/tourist/')
        assert b'Sign out' in response.data

        response = c.get('/tourist/place/au')
        assert b'Sign out' in response.data

        response = c.get(f'/admin/place/edit/?id={au.id}')
        assert response.status_code == 200
        assert b'Sign out' in response.data

        response = c.post(f'/admin/place/edit/?id={au.id}', data=dict(
            parent=au.parent_id,
            name=au.name + ' Changed',
            short_name=au.short_name,
        ))
        assert response.status_code == 302

        response = c.get('/admin/comment/')
        assert response.status_code == 200
        assert b'Sign out' in response.data

        response = c.get('/tourist/comments')
        assert response.status_code == 200

    with test_app.app_context():
        new_au = tstore.Place.query.filter_by(short_name='au').one()
        assert new_au.name == 'Australia Changed'
