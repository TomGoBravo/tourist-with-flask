import datetime
import operator
import re
from collections import defaultdict
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Type

import attr
import attrs
import click
import sqlalchemy_continuum
from flask.cli import AppGroup
import flask
from geoalchemy2.shape import to_shape
from more_itertools import last

import tourist
from tourist import render_factory
from tourist.continuumutils import ClubVersion
from tourist.continuumutils import PlaceVersion
from tourist.continuumutils import PoolVersion
from tourist.continuumutils import Transaction
from tourist.continuumutils import type_to_version_cls
from tourist.models import attrib
from tourist.models import tstore
from tourist.models.tstore import PAGE_LINK_RE
from tourist.scripts import sync

batchtool_cli = AppGroup('batchtool')


@attr.s(auto_attribs=True, slots=True)
class ClubPoolLink:
    club: tstore.Club
    target_short_name: str
    title: Optional[str]

    def __str__(self):
        return f"Link on {self.club.path} to {self.target_short_name}"


@batchtool_cli.command('club-pool-links')
def club_pool_links():
    links = []
    for club in tstore.Club.query.all():
        for link_title, link_target in re.findall(PAGE_LINK_RE, club.markdown):
            links.append(ClubPoolLink(club, link_target, link_title))
        for link_target in re.findall(r'\[\[(\w+)]]', club.markdown):
            links.append(ClubPoolLink(club, link_target, None))

    pools = {}
    for pool in tstore.Pool.query.all():
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
    for club in tstore.Club.query.all():
        new_markdown, sub_count = re.subn(PAGE_LINK_RE, r'[[\2]]', club.markdown)
        if sub_count > 0:
            club.markdown = new_markdown
            tstore.db.session.add(club)
            modified_clubs.append(club.short_name)
        replacements += sub_count

    click.echo(f'Replacing {replacements} links in {", ".join(modified_clubs)}')

    if write:
        click.echo('Committing changes')
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)
    else:
        click.echo('Run with --write to commit changes')


@batchtool_cli.command('check-comment-spam')
@click.option('--write', is_flag=True)
def check_comment_spam(write: bool):
    comment: tstore.PlaceComment
    for comment in tstore.PlaceComment.query.filter_by(akismet_spam_status=None).limit(50):

        if comment.akismet_spam_status is None and comment.content:
            comment.akismet_spam_status = tourist.get_comment_spam_status(comment)
            click.echo(f"{comment}: {comment.akismet_spam_status}")

    if write:
        click.echo('Committing changes')
        tstore.db.session.commit()
    else:
        click.echo('Run with --write to commit changes')


@batchtool_cli.command('validate')
def validate():
    for place in tstore.Place.query.all():
        place.validate()

    for club in tstore.Club.query.all():
        club.validate()

    for pool in tstore.Pool.query.all():
        pool.validate()


@batchtool_cli.command('render-cache')
def render_cache():
    with tstore.db.session.begin():
        tstore.db.session.add_all(render_factory.yield_cache())


@batchtool_cli.command('transactionshift')
@click.option('--write', is_flag=True)
def transactionshift(write: bool):
    import logging
    logging.basicConfig()
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
    shift_amount = 1123

    # From https://stackoverflow.com/a/2337965/341400
    def incr_column(cls, column_name: str):
        # Temporary offset suggested by https://stackoverflow.com/a/22500510/341400 to avoid
        # id collisions.
        temp_offset = 100_000
        tstore.db.session.query(cls).update({
            column_name: getattr(cls, column_name) + temp_offset})
        tstore.db.session.query(cls).update({
            column_name: getattr(cls, column_name) - temp_offset + shift_amount})

    for model_cls in (tstore.Club, tstore.Pool, tstore.Place):
        version_cls = sqlalchemy_continuum.version_class(model_cls)
        incr_column(version_cls, 'transaction_id')
        incr_column(version_cls, 'end_transaction_id')

    incr_column(sqlalchemy_continuum.transaction_class(tstore.Club), 'id')

    if write:
        click.echo('Committing changes')
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)
    else:
        click.echo('Run with --write to commit changes')


operation_type_column_name = sqlalchemy_continuum.utils.option(tstore.Club,
                                                         'operation_type_column_name')


@attr.s(auto_attribs=True)
class IdGuesser:
    type_short_name_to_id: Dict[str, Dict[str, int]] = attr.ib(factory=lambda: defaultdict(dict))
    type_name_to_id: Dict[str, Dict[str, int]] = attr.ib(factory=lambda: defaultdict(dict))

    def add_ids(self, entities: List[attrib.Entity]):
        for e in entities:
            existing_id = self.type_short_name_to_id[e.type].get(e.short_name, None)
            if existing_id is not None and e.id != existing_id:
                raise ValueError(f"Id changed for {e} from {existing_id}")
            self.type_short_name_to_id[e.type][e.short_name] = e.id

            if e.name not in ('noname club', 'Acre'):
                existing_id = self.type_name_to_id[e.type].get(e.name, None)
                if existing_id is not None and e.id != existing_id:
                    ValueError(f"Id changed for {e} from {existing_id}")
                self.type_name_to_id[e.type][e.name] = e.id
        assert set(self.type_short_name_to_id.keys()) == {'pool', 'place', 'club'}

    def guess_ids(self, e: attrib.Entity) -> attrib.Entity:
        guesses = {}
        if e.type == 'pool' and e.name.startswith("Lochot"):
            e.id = 247
        if e.type == 'pool' and e.name.startswith("Burpengary Regional Aquatic"):
            e.id = 6


        if e.parent_short_name and e.parent_id is None:
            guesses['parent_id'] = self.type_short_name_to_id['place'][e.parent_short_name]

        if e.id is None:
            by_short_name = self.type_short_name_to_id[e.type].get(e.short_name, None)
            if by_short_name:
                guesses['id'] = by_short_name
            else:
                guesses['id'] = self.type_name_to_id[e.type][e.name]

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

        sqlalchemy_kwargs = entity.sqlalchemy_kwargs_with_ids()
        new_version_object = self.version_cls(**sqlalchemy_kwargs)
        # operation_type and transaction_id raise 'invalid keyword argument for PlaceVersion'
        # when passed to the constructor so instead set them after the object is created.
        if entity.id in self.versions:
            new_version_object.operation_type = sqlalchemy_continuum.Operation.INSERT
        else:
            new_version_object.operation_type = sqlalchemy_continuum.Operation.UPDATE
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

    def live_versions(self):
        for version_obj in self.latest_versions():
            if version_obj.operation_type != sqlalchemy_continuum.Operation.DELETE:
                yield version_obj


@attr.s(auto_attribs=True)
class VersionSyncer:
    """Creates version history
    """
    version_tables: Dict[Type, VersionTable]
    new_transaction_ids: Set[int] = attr.ib(factory=set)

    @staticmethod
    def make() -> 'VersionSyncer':
        version_tables = {version_cls: VersionTable(entity_types={type_str}, version_cls=version_cls)
                          for type_str, version_cls in type_to_version_cls.items()}
        return VersionSyncer(version_tables=version_tables)

    def add_entity(self, e: attrib.Entity, transaction: Transaction):
        version_cls = type_to_version_cls[e.type]
        self.version_tables[version_cls].add_entity(transaction, e)
        self.new_transaction_ids.add(transaction.id)

    def replay_transaction(self, transaction: Transaction):
        assert transaction.id not in self.new_transaction_ids
        for version_cls, cls_changed_entities in transaction.changed_entities.items():
            for version_obj in cls_changed_entities:
                self.version_tables[version_cls].add_version_object(transaction, version_obj)

    def snapshot_entities(self):
        place_id_to_short_name = {
            place.id: place.short_name for place in self.version_tables[PlaceVersion].latest_versions()
        }
        # Special case expected only for place with short_name 'world'
        place_id_to_short_name[None] = ''
        entities = []
        for version_obj in self.version_tables[PoolVersion].live_versions():
            parent_short_name = place_id_to_short_name[version_obj.parent_id]
            entities.append(tstore.pool_as_attrib_entity(version_obj, parent_short_name))
        for version_obj in self.version_tables[ClubVersion].live_versions():
            parent_short_name = place_id_to_short_name[version_obj.parent_id]
            entities.append(tstore.club_as_attrib_entity(version_obj, parent_short_name))
        for version_obj in self.version_tables[PlaceVersion].live_versions():
            parent_short_name = place_id_to_short_name[version_obj.parent_id]
            entities.append(tstore.place_as_attrib_entity(version_obj, parent_short_name))
        return sync.sort_entities(entities)


@attr.s(auto_attribs=True)
class ChangeFromLog:
    issued_at: datetime.datetime
    user_id: int
    entity: attrib.Entity


def extract_logs(change_log_path: str, id_guesser: IdGuesser):
    for line in open(change_log_path).readlines():
        m = re.fullmatch(r'\[([\d\:\,\. -]+)\].+Change by <User (\d+)>: (\{.+\})\s*$', line)
        if not m:
            raise ValueError(f"Couldn't match '{line}'")
        # Log has , decimal separator
        dt = datetime.datetime.fromisoformat(m.group(1).replace(',', '.'))
        user_id = int(m.group(2))
        entity = attrib.Entity.load_from_jsons(m.group(3))
        if entity.short_name == 'remove':
            continue
        entity = id_guesser.guess_ids(entity)
        change = ChangeFromLog(dt, user_id, entity)
        yield change


def group_changes(changes_in: Iterable[ChangeFromLog]) -> Iterable[List[ChangeFromLog]]:
    # Group changes within one second of each other
    group = []
    for c in changes_in:
        if group and abs(c.issued_at.timestamp() - last(group).issued_at.timestamp()) > 1:
            yield group
            group = []
        group.append(c)
    if group:
        yield group


@batchtool_cli.command('transactioninsert1')
@click.option('--initial-snapshot', default='tourist-20190312T091406.jsonl')
@click.argument('output_path')
@click.option('--change-log', default='tourist-20220207-change-by.log')
@click.option('--commit', is_flag=True)
@click.option('--extra-ids', default='tourist-20220207T0753.jsonl')
def transactioninsert1(initial_snapshot: str, change_log: str, output_path: str,
                       extra_ids: str, commit: bool):
    """Attempt at inserting changes from a log file, abandoned because when I finally got it
    running the log file was missing details for every insert, rendering the transaction log
    very incomplete and even more of a mess to clean up."""
    import logging
    logging.basicConfig()
    logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

    jsonlines = open(initial_snapshot).readlines()
    entities_for_inital_snapshot = [attrib.Entity.load_from_jsons(j) for j in jsonlines]

    id_guesser = IdGuesser()
    id_guesser.add_ids(entities_for_inital_snapshot)

    if extra_ids:
        extra_for_ids = [attrib.Entity.load_from_jsons(j) for j in open(extra_ids).readlines()]
        id_guesser.add_ids(extra_for_ids)

    syncer = VersionSyncer.make()

    next_transaction_id = 1
    initial_snapshot_issued_at = datetime.datetime.fromisoformat("2019-03-12T00:00:00")
    entities_for_inital_snapshot = [id_guesser.guess_ids(e) for e in entities_for_inital_snapshot]
    for e in entities_for_inital_snapshot:
        syncer.add_entity(e, Transaction(id=next_transaction_id, issued_at=initial_snapshot_issued_at))
        next_transaction_id += 1

    for changes_list in group_changes(extract_logs(change_log, id_guesser)):
        transaction = Transaction(id=next_transaction_id, issued_at=changes_list[0].issued_at,
                                  user_id=changes_list[0].user_id)
        next_transaction_id += 1
        for change in changes_list:
            syncer.add_entity(change.entity, transaction)

    existing_transactions = Transaction.query.all()
    assert next_transaction_id <= min(e.id for e in existing_transactions)
    # Replay transactions to set the end_transaction_id on version objects that are changed
    # later.
    for t in existing_transactions:
        syncer.replay_transaction(t)

    out = open(output_path, 'w')
    for e in syncer.snapshot_entities():
        out.write(e.dump_as_jsons() + '\n')

    if commit:
        click.echo('Committing changes')
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)
    else:
        click.echo('Run with --write to commit changes')


@batchtool_cli.command('remove-empty-places')
@click.argument('descendants-of-short-name')
@click.option('--write', is_flag=True)
def remove_empty_places(descendants_of_short_name: str, write: bool):
    base_place = tstore.Place.query.filter_by(short_name=descendants_of_short_name).one()

    def _find_empty(place: tstore.Place) -> Tuple[bool, List]:
        results = []
        empty = not(place.child_pools or place.child_clubs or place.markdown)
        for child_place in place.child_places:
            child_is_empty, empty_descendants = _find_empty(child_place)
            if not child_is_empty:
                empty = False
            results.extend(empty_descendants)
        if empty:
            results.append(place)
        return empty, results

    _, empty_places = _find_empty(base_place)

    click.echo(f'Found empty places {", ".join(sorted(place.name for place in empty_places))}')

    if write:
        for place in empty_places:
            tstore.db.session.delete(place)
        click.echo('Committing changes')
        tstore.db.session.commit()
        tourist.update_render_cache(tstore.db.session)
    else:
        click.echo('Run with --write to commit changes')


@attrs.frozen(order=True)
class EntityMeasure:
    entity: tstore.Entity = attrs.field(order=False)
    # measure units depend on the context
    measure: float = attrs.field(order=True)


@batchtool_cli.command('check-geo',
                       help='Check the geometry for all descendants of given place.')
@click.argument('descendants-of-short-name')
def check_geo(descendants_of_short_name: str):
    base_place = tstore.Place.query.filter_by(short_name=descendants_of_short_name).one()
    places_parent_intersection = []
    pool_parent_distance = []

    def _check_place(place: tstore.Place) -> None:
        place_poly = to_shape(place.region)
        for child_place in place.child_places:
            child_poly = to_shape(child_place.region)
            part_in_parent = place_poly.intersection(child_poly).area / child_poly.area
            places_parent_intersection.append(EntityMeasure(child_place, part_in_parent))
            _check_place(child_place)

        for child_pool in place.child_pools:
            pool_pt = to_shape(child_pool.entrance)
            dist = place_poly.distance(pool_pt)
            pool_parent_distance.append(EntityMeasure(child_pool, dist))

    _check_place(base_place)

    ed: EntityMeasure
    click.echo("Places, by intersection ratio with parent. 1 for places in parent, smaller is "
               "worse.")
    for ed in sorted(places_parent_intersection)[:10]:
        click.echo(f"{ed.entity.short_name} in {ed.entity.parent.short_name}: {ed.measure}")

    click.echo("\nPools, by distance from parent. 0 for pools in the place, "
               "the units are fairly meaningless degrees but bigger means worse.")
    for ed in sorted(pool_parent_distance, reverse=True)[:10]:
        click.echo(f"{ed.entity.short_name} in {ed.entity.parent.short_name}: {ed.measure}")
