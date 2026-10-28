"""
Common types, defaults and functions used across files.
"""

import re
from typing import Literal, TypedDict

from shapely import LineString


class EdgeDefinitions(TypedDict):
    major: list[str]
    minor: list[str]
    pedestrian: list[str]


class NodeDefinitions(TypedDict):
    safe: list[str]
    unsafe: list[str]
    

class IntermediateEdgeData(TypedDict):
    type: Literal["cross", "walk"]
    side: Literal["clockwise", "counterclockwise", "left", "right", "center"]
    edge_osmid: int
    edge_highway: str
    edge_name: str
    edge_crossable: bool
    edge_walkable: Literal["neither", "left", "right", "both"]
    length: float
    geometry: LineString
    node_safety: Literal["safe", "unsafe", "neutral"] | None
    node_crossing: Literal["traffic_signals", "uncontrolled", "unmarked", "no"] | None
    

DEFAULT_ROAD_DEFINITIONS: EdgeDefinitions = {
    "major": ["motorway", "trunk", "primary", "secondary", "tertiary", "busway"],
    "minor": ["residential", "unclassified"],
    "pedestrian": ["footway", "pedestrian", "path", "steps", "corridor"],
}

DEFAULT_CROSSING_DEFINITIONS: NodeDefinitions = {
    "safe": ["traffic_signals", "uncontrolled", "unmarked"],
    "unsafe": ["no"],
}

DEFAULT_UNWALKABLE_PATS = ["motorway", "trunk", "busway", "cycleway"]


def match_road(highway_value: str, valid_pats: list[str]):
    """
    Returns True if `highway_value` matches one of `valid_pats`, False
    otherwise.
    """

    return re.match("|".join(valid_pats), highway_value) is not None