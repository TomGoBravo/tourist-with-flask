from collections import defaultdict
from typing import Any
from typing import Dict
from typing import List
from typing import Set
from typing import Type

import attrs
import sqlalchemy
import sqlalchemy_continuum
from more_itertools import last

from tourist.models import tstore

PoolVersion = sqlalchemy_continuum.version_class(tstore.Pool)
PlaceVersion = sqlalchemy_continuum.version_class(tstore.Place)
ClubVersion = sqlalchemy_continuum.version_class(tstore.Club)
Transaction = sqlalchemy_continuum.transaction_class(tstore.Club)


type_to_version_cls = {
    'place': PlaceVersion,
    'pool': PoolVersion,
    'club': ClubVersion,
}



@attrs.frozen()
class VersionTable:
    """In-memory copy of one Version table, built up while replaying transactions
    """
    entity_types: Set[str]
    version_cls: Type
    versions: Dict[int, List] = attrs.field(factory=lambda: defaultdict(list))

    def add_version_object(self, transaction: Transaction, new_version_obj):
        prev_version = last(self.versions[new_version_obj.id], None)
        if prev_version:
            assert prev_version.end_transaction_id == transaction.id
        self.versions[new_version_obj.id].append(new_version_obj)


@attrs.frozen()
class VersionTables:
    """In memory dump of continuum versions and transactions, created to make iterating through
    them run about 60 times faster. There is similar code in `batchtool`.

    TODO(TomGoBravo): add some tests for this
    """
    version_tables: Dict[Type, VersionTable]
    transaction_user_email: Dict[int, str] = attrs.field(factory=dict)
    transaction_issued_at: Dict[int, Any] = attrs.field(factory=dict)

    @staticmethod
    def make() -> 'VersionSyncer':
        version_tables = {version_cls: VersionTable(entity_types={type_str}, version_cls=version_cls)
                          for type_str, version_cls in type_to_version_cls.items()}
        return VersionTables(version_tables=version_tables)

    def populate(self):
        existing_transactions = Transaction.query.all()
        for transaction in existing_transactions:
            if transaction.user:
                self.transaction_user_email[transaction.id] = transaction.user.email
            self.transaction_issued_at[transaction.id] = transaction.issued_at
            for version_cls, cls_changed_entities in transaction.changed_entities.items():
                for version_obj in cls_changed_entities:
                    self.version_tables[version_cls].add_version_object(transaction, version_obj)

    def get_object_history(self, obj):
        obj_version_type = sqlalchemy_continuum.version_class(obj.__class__)
        version_table = self.version_tables[obj_version_type]
        return version_table.versions[obj.id]


def changeset(current_version, previous_version):
    """
    Return a dictionary of changed fields in this version with keys as
    field names and values as lists with first value as the old field value
    and second list value as the new value.

    This is a very ugly copy of sqlalchemy_continuum.version.VersionClassBase which I created
    because accessing the previous version is super slow.
    """
    data = {}

    for key in sqlalchemy.inspect(current_version.__class__).columns.keys():
        if sqlalchemy_continuum.utils.is_internal_column(current_version, key):
            continue
        if not previous_version:
            old = None
        else:
            old = getattr(previous_version, key)
        new = getattr(current_version, key)
        if old != new:
            data[key] = [old, new]
    return data
