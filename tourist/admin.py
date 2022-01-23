import flask
import flask_admin
from flask import url_for, redirect, request, current_app
from flask_admin.contrib import geoa
from flask_login import current_user
from wtforms import fields, widgets

import tourist
from tourist.models import sqlalchemy
import flask_pagedown.widgets
import flask_pagedown.fields

pagedown = flask_pagedown.PageDown()


class TouristAdminBaseModelView(geoa.ModelView):
    column_filters = ['parent_id', 'name', 'short_name']
    form_overrides = dict(markdown=flask_pagedown.fields.PageDownField)
    form_widget_args = {'markdown': {'rows': 6, 'style': 'width: 500px'}}
    column_exclude_list = ['versions', 'geocode_result_json', 'geocode_request',
                           'child_places', 'child_clubs', 'geonames_id', 'geocode_result_time']
    can_set_page_size = True

    edit_template = 'my_admin_edit.html'

    # From https://docs.mapbox.com/help/troubleshooting/migrate-legacy-static-tiles-api/
    # Used in flask-admin/flask_admin/contrib/geoa and flask_admin/static/admin/js/form.js
    # form.js pre-pends '//' so start the URL with the hostname.
    tile_layer_url = 'api.mapbox.com/styles/v1/mapbox/streets-v11/tiles/{z}/{x}/{y}?access_token={accessToken}'
    tile_layer_attribution = '© <a href="https://www.mapbox.com/about/maps/">Mapbox</a> © <a href="http://www.openstreetmap.org/copyright">OpenStreetMap</a> <strong><a href="https://www.mapbox.com/map-feedback/" target="_blank">Improve this map</a></strong>'

    def is_accessible(self):
        return current_user.is_authenticated and current_user.edit_granted

    def inaccessible_callback(self, name, **kwargs):
        return tourist.inaccessible_response()

    def create_form(self):
        form = super(TouristAdminBaseModelView, self).create_form()
        parent_short_name = request.args.get('parent')
        if parent_short_name:
            parent_obj = sqlalchemy.Place.query.filter_by(short_name=parent_short_name).one()
            form.parent.data = parent_obj
        return form


class PlaceAdminModelView(TouristAdminBaseModelView):
    column_list = ['name', 'short_name', 'region', 'markdown', 'parent']


class ClubAdminModelView(TouristAdminBaseModelView):
    column_list = ['name', 'short_name', 'markdown', 'parent']


class PoolAdminModelView(TouristAdminBaseModelView):
    column_list = ['name', 'short_name', 'entrance', 'markdown', 'parent']


admin_views = flask_admin.Admin(base_template='my_master.html')
admin_views.add_view(PlaceAdminModelView(sqlalchemy.Place, sqlalchemy.db.session, name='Place', endpoint='place'))
admin_views.add_view(ClubAdminModelView(sqlalchemy.Club, sqlalchemy.db.session, name='Club', endpoint='club'))
admin_views.add_view(PoolAdminModelView(sqlalchemy.Pool, sqlalchemy.db.session, name='Pool', endpoint='pool'))
