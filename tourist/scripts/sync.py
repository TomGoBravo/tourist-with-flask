from typing import Collection
from typing import Dict, Iterable, List, Set
from typing import Optional
from typing import Tuple

import shapely
import shapely.wkt
from flask.cli import AppGroup
import click
from prefect.deployments import Deployment

import tourist
from tourist.models import tstore, attrib
from geoalchemy2.shape import to_shape
import attr
from collections import defaultdict


sync_cli = AppGroup('sync')


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


def sync_club(new_club: tstore.Club, old_club: tstore.Club, ignore_columns=Collection[str]) -> \
        Tuple[bool, Set[str]]:
    updated = False
    club_columns = set()
    for col in tstore.Club.__table__.columns:
        name = col.name
        if name in ignore_columns:
            continue
        club_columns.add(name)
        old_value = getattr(old_club, name)
        new_value = getattr(new_club, name)
        if old_value != new_value:
            setattr(old_club, name, new_value)
            updated = True
    return updated, club_columns


def sync_pool_objects(new_pool, old_pool, ignore_columns=('id', )) -> List[str]:
    updated_columns = []
    for col in tstore.Pool.__table__.columns:
        name = col.name
        if name in ignore_columns:
            continue
        old_value = getattr(old_pool, name)
        new_value = getattr(new_pool, name)
        if name == 'entrance' and _geom_eq(old_value, new_value):
            continue
        if old_value != new_value:
            setattr(old_pool, name, new_value)
            updated_columns.append(name)
    return updated_columns


@attr.s(auto_attribs=True)
class StaticSyncer:
    """Sync updated objects from JSON, remapping ids as needed.

    This class does not communicate with the database.
    """
    short_name_to_place: Dict[str, tstore.Place] = attr.ib(factory=dict)
    short_name_to_club: Dict[str, tstore.Club] = attr.ib(factory=dict)
    short_name_to_pool: Dict[str, tstore.Pool] = attr.ib(factory=dict)
    to_add: List[tstore.Place] = attr.ib(factory=list)
    updated_fields: Set[str] = attr.ib(factory=set)
    skipped_type: Set[str] = attr.ib(factory=set)
    club_columns: Set[str] = attr.ib(factory=set)

    def add_existing_place(self, place: tstore.Place):
        self.short_name_to_place[place.short_name] = place

    def add_existing_club(self, club: tstore.Club):
        self.short_name_to_club[club.short_name] = club

    def add_existing_pool(self, pool: tstore.Pool):
        self.short_name_to_pool[pool.short_name] = pool

    def _place_or_place_id_by_short_name(self, short_name) -> Tuple[Optional[tstore.Place],
                                                                    Optional[int]]:
        place = self.short_name_to_place[short_name]
        if place.id is None:
            # place is likely newly created. Return an instance. If place is stored then
            # transient/detached objects that refer to place will also be stored by sqlalchemy
            # cascading logic and most likely that is desired because they are all being imported
            # and the database will assign ids for them.
            return place, None
        else:
            # place is likely read from the database. Use the raw id as an attribute of a
            # transient/detached object so sqlalchemy's cascading logic doesn't add it.
            return None, place.id

    def _new_place(self, a: attrib.Entity):
        if a.parent_short_name:
            parent, parent_id = self._place_or_place_id_by_short_name(a.parent_short_name)
        else:
            assert a.short_name == 'world'
            parent, parent_id = None, None

        return tstore.Place(
            parent=parent,
            parent_id=parent_id,
            **a.sqlalchemy_kwargs()
        )

    def _new_club(self, a: attrib.Entity):
        # Parent is always a place
        parent, parent_id = self._place_or_place_id_by_short_name(a.parent_short_name)

        return tstore.Club(
            # id isn't set
            parent=parent,
            parent_id=parent_id,
            **a.sqlalchemy_kwargs()
        )

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
            _, club_columns = sync_club(new_club, old_club, ignore_columns=('id',))
            self.club_columns.update(club_columns)
        else:
            self.to_add.append(new_club)
            self.short_name_to_club[new_club.short_name] = new_club

    def update_place(self, entity: attrib.Entity):
        new_place = self._new_place(entity)
        if entity.short_name in self.short_name_to_place:
            old_place = self.short_name_to_place[entity.short_name]
            updated = False
            #for name in dir(new_place):
            for col in tstore.Place.__table__.columns:
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
                    if _geom_eq(old_value, new_value):
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
            self.to_add.append(new_place)
            self.short_name_to_place[new_place.short_name] = new_place

    def update_pool(self, entity: attrib.Entity):
        new_pool = self._new_pool(entity)
        if entity.short_name in self.short_name_to_pool:
            old_pool = self.short_name_to_pool[entity.short_name]
            updated_columns = sync_pool_objects(new_pool, old_pool)
            if updated_columns:
                self.to_add.append(old_pool)
                print(f'Updating {old_pool} fields {",".join(updated_columns)}')
        else:
            self.to_add.append(new_pool)
            self.short_name_to_pool[new_pool.short_name] = new_pool

    def _new_pool(self, a: attrib.Entity):
        # Parent is always a place
        parent, parent_id = self._place_or_place_id_by_short_name(a.parent_short_name)

        return tstore.Pool(
            # id isn't set
            parent=parent,
            parent_id=parent_id,
            **a.sqlalchemy_kwargs()
        )


@attr.s(auto_attribs=True)
class Importer:
    skipped: List[str] = attr.ib(factory=list)
    updater: StaticSyncer = attr.ib(factory=StaticSyncer)

    def run(self, jsons_iterable: Iterable[str]):
        all_jsons = [*jsons_iterable]
        print(f'Going to import {len(all_jsons)} lines')

        for p in tstore.Place.query.all():
            self.updater.add_existing_place(p)
        for c in tstore.Club.query.all():
            self.updater.add_existing_club(c)
        for p in tstore.Pool.query.all():
            self.updater.add_existing_pool(p)

        for j in all_jsons:
            self.updater.update_entity(attrib.Entity.load_from_jsons(j))
        print('Skipped types: ' + ','.join(set(self.skipped)))
        print('Adding ' + str(len(self.updater.to_add)))
        print('Updated fields ' + ','.join(self.updater.updated_fields))
        tstore.db.session.add_all(self.updater.to_add)
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)


@sync_cli.command('import_jsonl')
@click.argument('input_path')
def import_jsonl(input_path):
    importer = Importer()
    importer.run(open(input_path).readlines())


def sort_entities(entities):
    children = defaultdict(list)
    shortname_list = defaultdict(list)
    for a in entities:
        shortname_list[a.short_name].append(a)
        children[a.parent_short_name].append(a.short_name)

    by_shortname = {}
    duplicates = {}
    for short_name, short_name_entities in shortname_list.items():
        if len(short_name_entities) == 1:
            by_shortname[short_name] = short_name_entities[0]
        else:
            duplicates[short_name] = short_name_entities
    if duplicates:
        raise ValueError(f'Duplicates: {duplicates}')

    # Only 'world' has parent_id ''. Check that it is the only child of ''.
    if children[''] != ['world', ]:
        raise ValueError(f'Expected only child of "" is "world" but found {children[""]}')

    def parents_first(by_shortname, children, shortname=''):
        yield by_shortname[shortname]
        for child_name in sorted(children[shortname]):
            for x in parents_first(by_shortname, children, child_name):
                yield x
    sorted_entities = [e for e in parents_first(by_shortname, children, 'world')]
    assert len(sorted_entities) == len(by_shortname)  == len(entities)
    assert {a.short_name for a in sorted_entities} == set(by_shortname.keys())
    return sorted_entities


def get_sorted_entities():
    entities: List[attrib.Entity] = []
    for p in tstore.Place.query.all():
        entities.append(p.as_attrib_entity())
    for c in tstore.Club.query.all():
        entities.append(c.as_attrib_entity())
    for pl in tstore.Pool.query.all():
        entities.append(pl.as_attrib_entity())
    return sort_entities(entities)


@sync_cli.command('extract')
@click.argument('output_path')
def extract(output_path):
    out = open(output_path, 'w')
    for e in get_sorted_entities():
        out.write(e.dump_as_jsons() + '\n')


def _output_place(place_short_name: List[str]):
    for short_name in place_short_name:
        p: tstore.Place = tstore.Place.query.filter_by(short_name=short_name).one()
        if p.region:
            region_wkt = shapely.wkt.dumps(to_shape(p.region), rounding_precision=2)
            region_attr = f", region=WKTElement('{region_wkt}', srid=4326))"
        else:
            region_attr = ''
        print(f"Place(name='{p.name}', short_name='{p.short_name}'{region_attr})")


@sync_cli.command('output-place')
@click.option('--place-short-name', multiple=True, default=[])
def output_place(place_short_name):
    """Print a tstore.Place literal string, which can be handy for writing tests."""
    _output_place(place_short_name)


@sync_cli.command('deploy-dataflow')
def deploy_dataflow():
    # avoid circular import, oh Python.
    import tourist.scripts.dataflow
    Deployment.build_from_flow(
        flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
        name="run_gb_fetch_and_sync",
        work_queue_name="development",
        path='/workspaces/tourist-with-flask',
    ).apply()
