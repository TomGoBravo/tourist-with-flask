from typing import Dict
from typing import List

import attrs


@attrs.frozen()
class Club:
    id: int
    name: str
    short_name: str
    markdown: str


@attrs.frozen()
class Pool:
    id: int
    name: str
    short_name: str
    markdown: str


@attrs.frozen()
class Place:
    id: int
    name: str
    short_name: str
    geojson_children_collection: Dict
    child_clubs: List[Club]
    child_pools: List[Pool]

    @property
    def geojson_children_collection(self):
        children_geojson = [p.entrance_geojson_feature for p in self.place._descendant_pools if
                            p.entrance_geojson_feature]
        if children_geojson:
            return geojson.FeatureCollection(children_geojson)
        else:
            return {}
