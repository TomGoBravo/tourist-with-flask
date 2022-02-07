import datetime
import re
from collections import defaultdict
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Set
from typing import Type

import attr
import click
import sqlalchemy_continuum
from flask.cli import AppGroup
from more_itertools import last

from tourist.models import attrib
from tourist.models import sqlalchemy
from tourist.models.sqlalchemy import PAGE_LINK_RE
from tourist.models.sqlalchemy import optional_geometry_to_shape
from tourist.scripts import sync

batchtool_cli = AppGroup('batchtool')


@attr.s(auto_attribs=True, slots=True)
class ClubPoolLink:
    club: sqlalchemy.Club
    target_short_name: str
    title: Optional[str]

    def __str__(self):
        return f"Link on {self.club.path} to {self.target_short_name}"


@batchtool_cli.command('club-pool-links')
def club_pool_links():
    links = []
    for club in sqlalchemy.Club.query.all():
        for link_title, link_target in re.findall(PAGE_LINK_RE, club.markdown):
            links.append(ClubPoolLink(club, link_target, link_title))
        for link_target in re.findall(r'\[\[(\w+)]]', club.markdown):
            links.append(ClubPoolLink(club, link_target, None))

    pools = {}
    for pool in sqlalchemy.Pool.query.all():
        pools[pool.short_name] = pool

    good_links = []
    not_found_links = []
    diff_place_links = []
    diff_title_links = []
    for link in links:
        if link.target_short_name not in pools:
            not_found_links.append(link)
            continue
        target_pool = pools[link.target_short_name]
        if target_pool.parent.short_name != link.club.parent.short_name:
            diff_place_links.append(link)
            continue
        if link.title and target_pool.name != link.title:
            # Output while target_pool is set
            click.echo(f'{link}: title {target_pool.name} != {link.title}')
            diff_title_links.append(link)
            continue
        good_links.append(link)
    click.echo('Link target short_name not found:')
    for link in not_found_links:
        click.echo(f'    {link}')
    click.echo('Link target in different place:')
    for link in diff_place_links:
        click.echo(f'    {link}')
    click.echo(f'{len(good_links)} good links')


@batchtool_cli.command('replace-club-pool-links')
@click.option('--write', is_flag=True)
def replace_club_pool_links(write):
    replacements = 0
    modified_clubs = []
    for club in sqlalchemy.Club.query.all():
        new_markdown, sub_count = re.subn(PAGE_LINK_RE, r'[[\2]]', club.markdown)
        if sub_count > 0:
            club.markdown = new_markdown
            sqlalchemy.db.session.add(club)
            modified_clubs.append(club.short_name)
        replacements += sub_count

    click.echo(f'Replacing {replacements} links in {", ".join(modified_clubs)}')

    if write:
        click.echo('Committing changes')
        sqlalchemy.db.session.commit()
    else:
        click.echo('Run with --write to commit changes')


@batchtool_cli.command('validate')
def validate():
    for place in sqlalchemy.Place.query.all():
        place.validate()

    for club in sqlalchemy.Club.query.all():
        club.validate()

    for pool in sqlalchemy.Pool.query.all():
        pool.validate()


@batchtool_cli.command('transactionshift')
@click.option('--write', is_flag=True)
def transactionshift(write: bool):
    import logging
    logging.basicConfig()
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

    # From https://stackoverflow.com/a/2337965/341400
    def incr_column(cls, column_name: str):
        # Temporary offset suggested by https://stackoverflow.com/a/22500510/341400 to avoid
        # id collisions.
        temp_offset = 10_000
        sqlalchemy.db.session.query(cls).update({
            column_name: getattr(cls, column_name) + temp_offset})
        sqlalchemy.db.session.query(cls).update({
            column_name: getattr(cls, column_name) - temp_offset + 1})

    for model_cls in (sqlalchemy.Club, sqlalchemy.Pool, sqlalchemy.Place):
        version_cls = sqlalchemy_continuum.version_class(model_cls)
        incr_column(version_cls, 'transaction_id')
        incr_column(version_cls, 'end_transaction_id')

    incr_column(sqlalchemy_continuum.transaction_class(sqlalchemy.Club), 'id')

    if write:
        click.echo('Committing changes')
        sqlalchemy.db.session.commit()
    else:
        click.echo('Run with --write to commit changes')


PoolVersion = sqlalchemy_continuum.version_class(sqlalchemy.Pool)
PlaceVersion = sqlalchemy_continuum.version_class(sqlalchemy.Place)
ClubVersion = sqlalchemy_continuum.version_class(sqlalchemy.Club)
Transaction = sqlalchemy_continuum.transaction_class(sqlalchemy.Club)
operation_type_column_name = sqlalchemy_continuum.utils.option(sqlalchemy.Club,
                                                         'operation_type_column_name')


@attr.s(auto_attribs=True)
class IdGuesser:
    type_short_name_to_id: Dict[str, Dict[str, int]] = attr.ib(factory=lambda: defaultdict(dict))

    def add_ids(self, entities: List[attrib.Entity]):
        for e in entities:
            self.type_short_name_to_id[e.type][e.short_name] = e.id
        assert set(self.type_short_name_to_id.keys()) == {'pool', 'place', 'club'}

    def guess_ids(self, e: attrib.Entity) -> attrib.Entity:
        guesses = {}
        if e.parent_short_name and e.parent_id is None:
            guesses['parent_id'] = self.type_short_name_to_id['place'][e.parent_short_name]

        if e.id is None:
            guesses['id'] = self.type_short_name_to_id[e.type][e.short_name]

        if guesses:
            return attr.evolve(e, **guesses)
        else:
            return e


@attr.s(auto_attribs=True)
class VersionTable:
    """In-memory copy of one Version table, built up while replaying transactions
    """
    entity_types: Set[str]
    version_cls: Type
    versions: Dict[int, List] = attr.ib(factory=lambda: defaultdict(list))
    new_objects: List = attr.ib(factory=lambda: list())

    def add_entity(self, transaction: Transaction, entity: attrib.Entity):
        assert entity.type in self.entity_types
        assert entity.id is not None
        if entity.parent_id is None:
            if entity.short_name != 'world':
                raise ValueError(f"Expected ever entity except world with parent_id: {entity}")

        sqlalchemy_kwargs = entity.sqlalchemy_kwargs()
        new_version_object = self.version_cls(**sqlalchemy_kwargs)
        # operation_type and transaction_id raise 'invalid keyword argument for PlaceVersion'
        # when passed to the constructor so instead set them after the object is created.
        if entity.id in self.versions:
            new_version_object.operation_type = sqlalchemy_continuum.operation.Operation.INSERT
        else:
            new_version_object.operation_type = sqlalchemy_continuum.operation.Operation.UPDATE
        new_version_object.transaction_id = transaction.id
        self.new_objects.append(new_version_object)
        self.add_version_object(transaction, new_version_object)

    def add_version_object(self, transaction: Transaction, new_version_obj):
        prev_version = last(self.versions[new_version_obj.id], None)
        if prev_version:
            if prev_version.end_transaction_id is None:
                # Only objects added with `add_entity` are expected to need this to be modified
                assert prev_version in self.new_objects
                prev_version.end_transaction_id = transaction.id
            else:
                assert prev_version.end_transaction_id == transaction.id
        self.versions[new_version_obj.id].append(new_version_obj)

    def latest_versions(self):
        return [last(version_list) for version_list in self.versions.values()]


type_to_version_cls = {
    'place': PlaceVersion,
    'pool': PoolVersion,
    'club': ClubVersion,
}


@attr.s(auto_attribs=True)
class VersionSyncer:
    """Creates version history
    """
    version_tables: Dict[Type, VersionTable]
    new_transaction_ids: List[int] = attr.ib(factory=list)

    @staticmethod
    def make() -> 'VersionSyncer':
        version_tables = {version_cls: VersionTable(entity_types={type_str}, version_cls=version_cls)
                          for type_str, version_cls in type_to_version_cls.items()}
        return VersionSyncer(version_tables=version_tables)

    def add_entity(self, e: attrib.Entity, transaction: Transaction):
        version_cls = type_to_version_cls[e.type]
        self.version_tables[version_cls].add_entity(transaction, e)
        self.new_transaction_ids.append(transaction.id)

    def replay_transaction(self, transaction: Transaction):
        for version_cls, cls_changed_entities in transaction.changed_entities.items():
            for version_obj in cls_changed_entities:
                self.version_tables[version_cls].add_version_object(transaction, version_obj)

    def snapshot_entities(self):
        place_id_to_short_name = {
            place.id: place.short_name for place in self.version_tables[PlaceVersion].latest_versions()
        }
        entities = []
        for version_obj in self.version_tables[PoolVersion].latest_versions():
            parent_short_name = place_id_to_short_name.get(version_obj.parent_id, '')
            entities.append(sqlalchemy.pool_as_attrib_entity(version_obj, parent_short_name))
        for version_obj in self.version_tables[ClubVersion].latest_versions():
            parent_short_name = place_id_to_short_name.get(version_obj.parent_id, '')
            entities.append(sqlalchemy.club_as_attrib_entity(version_obj, parent_short_name))
        for version_obj in self.version_tables[PlaceVersion].latest_versions():
            parent_short_name = place_id_to_short_name.get(version_obj.parent_id, '')
            entities.append(sqlalchemy.place_as_attrib_entity(version_obj, parent_short_name))
        return sync.sort_entities(entities)


@batchtool_cli.command('transactioninsert1')
@click.argument('input_path')
@click.argument('output_path')
@click.option('--commit', is_flag=True)
def transactioninsert1(input_path: str, output_path: str, commit: bool):
    import logging
    logging.basicConfig()
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)


    jsonlines = open(input_path).readlines()
    entities = [attrib.Entity.load_from_jsons(j) for j in jsonlines]

    transactions = Transaction.query.all()

    new_trans = sqlalchemy_continuum.transaction_class(sqlalchemy.Club)(id=1,
                                                                        issued_at=datetime.datetime.fromisoformat("2022-01-01"))
    assert new_trans.id not in {t.id for t in transactions}

    id_guesser = IdGuesser()
    id_guesser.add_ids(entities)
    entities = [id_guesser.guess_ids(e) for e in entities]

    syncer = VersionSyncer.make()

    for e in entities:
        syncer.add_entity(e, new_trans)

    # Replay transactions to set the end_transaction_id on version objects that are changed
    # later.
    for t in transactions:
        syncer.replay_transaction(t)

    out = open(output_path, 'w')
    for e in syncer.snapshot_entities():
        out.write(e.dump_as_jsons() + '\n')

    if commit:
        click.echo('Committing changes')
        sqlalchemy.db.session.commit()
    else:
        click.echo('Run with --write to commit changes')
