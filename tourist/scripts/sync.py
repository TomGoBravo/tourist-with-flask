from typing import Dict, Iterable, List, Set
from shapely.geometry import asShape
from geoalchemy2.shape import from_shape, to_shape
from flask.cli import AppGroup
import click
from tourist.models import sqlalchemy, attrib
from shapely.geometry import mapping as shapely_mapping
from geoalchemy2.shape import to_shape
import attr
from collections import defaultdict
import json


sync_cli = AppGroup('sync')


@attr.s(auto_attribs=True)
class StaticSyncer:
    """Sync updated objects from JSON, remapping ids as needed.

    This class does not communicate with the database.
    """
    short_name_to_place: Dict[str, sqlalchemy.Place] = attr.ib(factory=lambda: {'': None})
    short_name_to_club: Dict[str, sqlalchemy.Club] = attr.ib(factory=dict)
    short_name_to_pool: Dict[str, sqlalchemy.Pool] = attr.ib(factory=dict)
    to_add: List[sqlalchemy.Place] = attr.ib(factory=list)
    updated_fields: Set[str] = attr.ib(factory=set)
    skipped_type: Set[str] = attr.ib(factory=set)
    club_columns: Set[str] = attr.ib(factory=set)

    def add_existing_place(self, place: sqlalchemy.Place):
        self.short_name_to_place[place.short_name] = place

    def add_existing_club(self, club: sqlalchemy.Club):
        self.short_name_to_club[club.short_name] = club

    def add_existing_pool(self, pool: sqlalchemy.Pool):
        self.short_name_to_pool[pool.short_name] = pool

    def _new_place(self, a):
        if a.region:
            region = from_shape(asShape(a.region), srid=4326)
        else:
            region = None
        parent = self.short_name_to_place[a.parent_short_name]
        parent_id = parent and parent.id or None

        return sqlalchemy.Place(
            # id isn't set
            name=a.name,
            short_name=a.short_name,
            region=region,
            markdown=a.markdown,
            parent_id=parent_id,
            geonames_id=a.geonames_id,
            status_date=a.status_date,
            status_comment=a.status_comment,
            #geocode_request=a.geocode_request,
            #geocode_result_json=a.geocode_result_json,
            #geocode_result_time=a.geocode_result_time or None
        )

    def _new_club(self, a: attrib.Entity):
        # Parent is always a place
        parent = self.short_name_to_place[a.parent_short_name]
        parent_id = parent and parent.id or None

        return sqlalchemy.Club(
            # id isn't set
            name=a.name,
            short_name=a.short_name,
            markdown=a.markdown,
            parent_id=parent_id,
            status_date=a.status_date,
            status_comment=a.status_comment,
        )

    @staticmethod
    def _geom_eq(old_value, new_value):
        if old_value is None and new_value is None:
            return True
        if old_value is None or new_value is None:
            return False
        old_shape = to_shape(old_value)
        new_shape = to_shape(new_value)
        if old_shape == new_shape:
            return True
        else:
            return False

    def update_entity(self, entity: attrib.Entity):
        if entity.type in ('place', 'world', 'country', 'division', 'town'):
            self.update_place(entity)
        elif entity.type == 'club':
            self.update_club(entity)
        elif entity.type == 'pool':
            self.update_pool(entity)
        else:
            self.skipped_type.add(entity.type)

    def update_club(self, entity: attrib.Entity):
        new_club = self._new_club(entity)
        if entity.short_name in self.short_name_to_club:
            old_club = self.short_name_to_club[entity.short_name]
            updated = False
            for col in sqlalchemy.Club.__table__.columns:
                name = col.name
                if name in ('id', ):
                    continue
                self.club_columns.add(name)
                old_value = getattr(old_club, name)
                new_value = getattr(new_club, name)
                if old_value != new_value:
                    setattr(old_club, name, new_value)
                    updated = True
        else:
            new_club.id = len(self.short_name_to_club)
            self.to_add.append(new_club)
            self.short_name_to_club[new_club.short_name] = new_club

    def update_place(self, entity: attrib.Entity):
        new_place = self._new_place(entity)
        if entity.short_name in self.short_name_to_place:
            old_place = self.short_name_to_place[entity.short_name]
            updated = False
            #for name in dir(new_place):
            for col in sqlalchemy.Place.__table__.columns:
                name = col.name
                if name in ('id', ):
                    continue
                #if name in ('id', 'short_name', 'query', 'children', 'geojson_feature', 'versions', 'geojson_children_collection'):
                #    continue
                #if name.startswith('_'):
                #    continue
                old_value = getattr(old_place, name)
                new_value = getattr(new_place, name)
                if name == 'region':
                    if self._geom_eq(old_value, new_value):
                        continue
                    else:
                        if new_value:
                            setattr(old_place, name, new_value)
                            updated = True
                            self.updated_fields.add(name)
                elif new_value and old_value != new_value:
                    setattr(old_place, name, new_value)
                    updated = True
                    self.updated_fields.add(name)
            if updated:
                self.to_add.append(old_place)
        else:
            new_place.id = len(self.short_name_to_place)
            self.to_add.append(new_place)
            self.short_name_to_place[new_place.short_name] = new_place

    def update_pool(self, entity: attrib.Entity):
        new_pool = self._new_pool(entity)
        if entity.short_name in self.short_name_to_pool:
            old_pool = self.short_name_to_pool[entity.short_name]
            updated_columns = []
            for col in sqlalchemy.Pool.__table__.columns:
                name = col.name
                if name in ('id', ):
                    continue
                old_value = getattr(old_pool, name)
                new_value = getattr(new_pool, name)
                if name == 'entrance' and self._geom_eq(old_value, new_value):
                    continue
                if old_value != new_value:
                    setattr(old_pool, name, new_value)
                    updated_columns.append(name)
            if updated_columns:
                self.to_add.append(old_pool)
                print(f'Updating {old_pool} fields {",".join(updated_columns)}')

        else:
            new_pool.id = len(self.short_name_to_pool)
            self.to_add.append(new_pool)
            self.short_name_to_pool[new_pool.short_name] = new_pool

    def _new_pool(self, a: attrib.Entity):
        # Parent is always a place
        parent = self.short_name_to_place[a.parent_short_name]
        parent_id = parent and parent.id or None
        if a.point:
            entrance = from_shape(asShape(a.point), srid=4326)
        else:
            entrance = None

        return sqlalchemy.Pool(
            # id isn't set
            name=a.name,
            short_name=a.short_name,
            markdown=a.markdown,
            parent_id=parent_id,
            entrance=entrance,
            status_date=a.status_date,
            status_comment=a.status_comment,
        )


@attr.s(auto_attribs=True)
class Importer:
    skipped: List[str] = attr.ib(factory=list)
    updater: StaticSyncer = attr.ib(factory=StaticSyncer)

    def run(self, jsons_iterable: Iterable[str]):
        all_jsons = [*jsons_iterable]
        print(f'Going to import {len(all_jsons)} lines')

        for p in sqlalchemy.Place.query.all():
            self.updater.add_existing_place(p)
        for c in sqlalchemy.Club.query.all():
            self.updater.add_existing_club(c)
        for p in sqlalchemy.Pool.query.all():
            self.updater.add_existing_pool(p)

        for j in all_jsons:
            self.updater.update_entity(attrib.Entity.load_from_jsons(j))
        print('Skipped types: ' + ','.join(set(self.skipped)))
        print('Adding ' + str(len(self.updater.to_add)))
        print('Updated fields ' + ','.join(self.updater.updated_fields))
        sqlalchemy.db.session.add_all(self.updater.to_add)
        sqlalchemy.db.session.commit()


@sync_cli.command('import_jsonl')
@click.argument('input_path')
def import_jsonl(input_path):
    importer = Importer()
    importer.run(open(input_path).readlines())


def sort_entities(entities):
    unique_short_names = set()
    children = defaultdict(list)
    by_shortname = {}
    for a in entities:
        assert a.short_name not in unique_short_names
        unique_short_names.add(a.short_name)
        children[a.parent_short_name].append(a.short_name)
        by_shortname[a.short_name] = a
    # Only 'world' has parent_id ''. Check that it is the only child of ''.
    assert children[''] == ['world', ]

    def parents_first(by_shortname, children, shortname=''):
        yield by_shortname[shortname]
        for child_name in sorted(children[shortname]):
            for x in parents_first(by_shortname, children, child_name):
                yield x
    sorted_entities = [e for e in parents_first(by_shortname, children, 'world')]
    assert len(sorted_entities) == len(unique_short_names)  == len(entities)
    assert {a.short_name for a in sorted_entities} == unique_short_names
    return sorted_entities


def get_sorted_entities():
    entities: List[attrib.Entity] = []
    for p in sqlalchemy.Place.query.all():
        entities.append(p.as_attrib_entity())
    for c in sqlalchemy.Club.query.all():
        entities.append(c.as_attrib_entity())
    for pl in sqlalchemy.Pool.query.all():
        entities.append(pl.as_attrib_entity())
    return sort_entities(entities)


@sync_cli.command('extract')
@click.argument('output_path')
def extract(output_path):
    out = open(output_path, 'w')
    for e in get_sorted_entities():
        out.write(e.dump_as_jsons() + '\n')
