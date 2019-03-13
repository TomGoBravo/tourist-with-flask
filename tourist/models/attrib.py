from typing import Dict, Optional, List
from datetime import date
import attr
import json
from shapely.geometry.geo import shape
from shapely.geometry.geo import mapping
import geojson


def is_date_or_none(self, _, value):
    if value is None:
        return
    date.fromisoformat(value)


def date_handler(obj):
    if isinstance(obj, (date, )):
        return obj.isoformat()
    elif hasattr(obj, "__geo_interface__"):
        # There may be an extra round trip from geo to mapping but this works
        shp = shape(obj)
        fixed = geojson.utils.map_coords(lambda c: round(c, 6), mapping(shp))
        return fixed
    else:
        return None


def dump_filter(attribute: attr.Attribute, value):
    if attribute.name in ('skipped', ):
        return False
    if attribute.name in ('markdown', 'status_comment', 'status_date', 'geonames_id', 'region', 'point') and not value:
        return False
    return True


@attr.s(auto_attribs=True, slots=True)
class Entity:
    type: str = attr.ib(validator=attr.validators.in_(['place', 'club', 'pool']))
    name: str
    short_name: str
    parent_short_name: str
    markdown: str = ''
    status_comment: str = ''
    status_date: str = attr.ib(default=None, validator=is_date_or_none)

    geonames_id: int = 0
    region: Dict = attr.ib(factory=dict)
    point: Dict = attr.ib(factory=dict)

    skipped: List[str] = attr.ib(factory=list)

    def dump_as_jsons(self) -> str:
        return json.dumps(attr.asdict(self, filter=dump_filter), default=date_handler)

    @staticmethod
    def load_from_jsons(jsons: str) -> 'Entity':
        d = json.loads(jsons)
        skipped = []
        for name in list(d.keys()):
            if name not in attr.fields_dict(Entity):
                skipped.append(name)
                del d[name]
        d['skipped'] = skipped
        if d.get('region'):
            d['region'] = shape(d['region'])
        if d.get('point'):
            d['point'] = shape(d['point'])
        return Entity(**d)
