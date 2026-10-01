"""
Functions used to generate the crossing graph from an OSMnx street network
graph.
"""

import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Literal, TypedDict

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely import LineString, polygonize


class EdgeDefinitions(TypedDict):
    major: list[str]
    minor: list[str]


class NodeDefinitions(TypedDict):
    safe: list[str]
    unsafe: list[str]


class IntermediateNodeData(TypedDict): ...


class IntermediateEdgeData(TypedDict): ...


DEFAULT_ROAD_DEFINITIONS: NodeDefinitions = {
    "major": ["motor", "primary", "secondary", "tertiary"],
    "minor": ["residential", "cycleway"],
}

DEFAULT_CROSSING_DEFINITIONS: EdgeDefinitions = {
    "safe": ["traffic_signals", "uncontrolled", "unmarked"],
    "unsafe": ["no"],
}


def get_base_graph(
    loader_args,
    loader_fn: Callable[..., nx.MultiDiGraph] = ox.graph_from_polygon,
    cache_dir: Path = "cache",
):
    """
    Uses OSMnx to download the base graph with all streets and crossings for a
    certain region. This graph will be used to generate a new graph where
    movement is only possible along "safe" crossings.

    Parameters
    ---
    loader_args
        Positional arguments to be passed to `loader_fn`. If it is a tuple,
        then each element of the tuple is passed as a separate positional
        argument to `loader_fn`.
    loader_fn : callable, default: osmnx.graph_from_polygon
        OSMnx loader function. Must work when passed `loader_args` as
        positional arguments.
    cache_dir : Path, default: "cache"
        Path to save/load cached OSMnx downloads.
    """

    ox.settings.useful_tags_node = ["highway", "crossing", "crossing:signals"]
    ox.settings.useful_tags_way = [
        "highway",
        "name",
        "sidewalk",
        "sidewalk:both",
        "sidewalk:left",
        "sidewalk:right",
        "foot",
    ]
    ox.settings.cache_folder = cache_dir
    ox.settings.bidirectional_network_types.append("all")

    if not isinstance(loader_args, tuple):
        loader_args = (loader_args,)

    street_graph = loader_fn(*loader_args, network_type="all", simplify=False)
    street_graph = ox.simplify_graph(
        street_graph, edge_attrs_differ=["osmid"], node_attrs_include=["highway"]
    )

    return street_graph


def _match_road(highway_value: str, valid_pats: list[str]):
    """
    Returns True if `highway_value` matches one of `valid_pats`, False
    otherwise.
    """

    return re.match("|".join(valid_pats), highway_value) is not None


def _standardize_crossing_types(df_nodes: pd.DataFrame) -> pd.Series:
    """
    Standardizes all possible crossing types for each row in `df_nodes` into
    five categories:

    - `traffic_signals`: crossing that contain traffic signals.
    - `uncontrolled`: marked crossing with no traffic signals (e.g. zebra
      crossings).
    - `unmarked`: other crossings that are not explicitly painted into the
      street.
    - `no`: where crossing is not allowed.
    - `none`: where crossing is neither explicitly allowed not explicitly
      disallowed.

    Returns a new column with the standardized crossings.
    """

    conditions = {
        "traffic_signals": (
            df_nodes["crossing"].str.contains("traffic_signals")
            | df_nodes["highway"].str.contains("traffic_signals")
            | (df_nodes["crossing:signals"] == "yes")
        ),
        "uncontrolled": (
            df_nodes["crossing"].isin(["uncontrolled", "marked", "zebra"])
        ),
        "unmarked": (df_nodes["crossing"].str.contains("unmarked")),
        "no": (df_nodes["crossing"].isin["no", "separate"]),
    }

    return np.select(conditions.keys(), conditions.values(), "none")


def _update_traffic_signals(
    G: nx.MultiDiGraph,
    u: int,
    max_dist: float,
    edge_definitions: EdgeDefinitions,
    current_dist: float | None = None,
) -> None:
    """
    Recursively sets the distance to the nearest traffic signal for each node,
    up to a maximum distance. Stores this value in the node's
    `traffic_signal_distance` attribute.

    Parameters
    ---
    G : MultiDiGraph
        Street graph to update. Crossing types must be standardized.
    u : int
        Node to update in `G`.
    max_dist : float
        Maximum distance allowed for a traffic signal. If a node's nearest
        traffic signal is further away than `max_dist`, then it won't have a
        `traffic_signal_distance` attribute.
    edge_definitions : EdgeDefinitions
        Dictionary that defines major and minor roads. The distance to each
        traffic signal will only be measured along these roads (network
        distance, not euclidean distance).
    current_dist : float or None, default: None
        Distance to the closest traffic signal currently known.
    """

    # this is probably not a very efficient algorithm (we are potentially
    # revisiting the same nodes multiple times), but it works for what i'm
    # trying to do

    # if the node is a traffic signal crossing, its distance is 0
    if G.nodes[u].get("crossing") == "traffic_signals":
        G.add_node(u, traffic_signal_distance=0)
    # if we are given a current_dist, assign it
    elif current_dist is not None:
        G.add_node(
            u,
            traffic_signal_distance=current_dist,
        )

    # if the node is near a traffic signal, we might be able to "propagate"
    # the traffic signal to its neighbors
    current_u = G.nodes[u].get("traffic_signal_distance")
    if current_u is not None:
        for _, v, data in G.out_edges(u, data=True):
            current_v = G.nodes[v].get("traffic_signal_distance")
            candidate_v = data.get("length") + current_u
            highway = data.get("highway")

            # only propagate if both of the following are true:
            # - we are moving along a major or minor road
            # - the destination node's new closest traffic signal would be at
            #   most max_dist, and would be closer than the current closest
            #   traffic signal
            if _match_road(
                highway, edge_definitions["major"] + edge_definitions["minor"]
            ) and (
                (current_v is not None and candidate_v < current_v)
                or (current_v is None and candidate_v <= max_dist)
            ):
                # update the node (which might in turn update its neighbors)
                _update_traffic_signals(
                    G,
                    v,
                    max_dist=max_dist,
                    edge_definitions=edge_definitions,
                    current_dist=candidate_v,
                )


def _get_angle(geometry: LineString) -> float:
    """
    Gets the angle for a certain LineString object, calculated from its first
    two points, with respect to a horizontal east-west line that crosses the
    first point.
    """

    p0 = geometry.coords[0]
    p1 = geometry.coords[1]

    return np.arctan2(p1[1] - p0[1], p1[0] - p0[0])


def _get_walkable(edge: pd.Series) -> Literal["both", "left", "right", "neither"]:
    """
    Returns which sides an edge/road is walkable along. Can be either "both",
    "left", "right" or "neither".

    The side is calculated from the perspective starting at the first point in
    the edge, and ending at the last point in the edge (e.g. if the left side
    is walkable, you can walk along the edge while always being to the left of
    said edge if you were looking forward while walking).
    """

    foot = edge["foot"]

    # designated footpaths are always walkable on both sides
    if _match_road(edge["highway"], ["footway", "pedestrian"]) or foot in (
        "yes",
        "designated",
    ):
        return "both"

    # motorways and cycleways are never walkable
    if _match_road(edge["highway"], ["motor", "cycleway"]) or foot in (
        "no",
        "use_sidepath",
    ):
        return "neither"

    # we will only exclude a side if it is explicitly UNwalkable (we will
    # assume all sides are walkable unless shown otherwise)
    no_sidewalk = edge["sidewalk"] in ("no", "separate")
    no_sidewalk_both = edge["sidewalk:both"] in ("no", "separate")
    no_sidewalk_left = edge["sidewalk:left"] in ("no", "separate")
    no_sidewalk_right = edge["sidewalk:right"] in ("no", "separate")

    unwalkable_left = no_sidewalk or no_sidewalk_both or no_sidewalk_left

    unwalkable_right = no_sidewalk or no_sidewalk_both or no_sidewalk_right

    if edge["reversed"]:
        unwalkable_left, unwalkable_right = unwalkable_right, unwalkable_left

    match (unwalkable_left, unwalkable_right):
        case (True, True): return "neither"
        case (True, False): return "right"
        case (False, True): return "left"
        case _: return "both"


def _set_node_safety(
    gdf_nodes: gpd.GeoDataFrame, node_definitions: NodeDefinitions
) -> pd.Series:
    """
    Sets the safety for each node in `gdf_nodes` based on `node_defintions`,
    selecting the first key where one of its values exactly matches with the
    value in `gdf_nodes["crossing"]`. If none match, then the node is declared
    neutral by default.

    Returns a new column with the safety for each node.
    """

    conditions = {
        node: gdf_nodes["crossing"].isin(matches) for node, matches in node_definitions
    }

    return np.select(conditions.keys(), conditions.values(), "neutral")


def _set_edge_crossable(
    gdf_edges: gpd.GeoDataFrame, uncrossable_pats: list[str]
) -> pd.Series:
    """
    Sets whether each edge in `gdf_nodes` is crossable. If the value in
    `gdf_edges["highway"]` matches with one of the regex patterns in
    `uncrossable_pats`, then the edge is declared uncrossable. If none match,
    then the edge is declared crossable by default.

    Returns a new column with the "crossability" of each edge.
    """

    return np.where(
        gdf_edges["highway"].str.contains("|".join(uncrossable_pats)),
        False,
        True,
    )


def generate_crossing_graph(
    base_graph: nx.MultiDiGraph,
    node_definitions: NodeDefinitions | None = None,
    edge_definitions: EdgeDefinitions | None = None,
    unsafe_distance_from_signal: float = 20,
    fix_marked_crossings_near_signals: float | None = None,
):
    """
    TODO
    """

    if node_definitions is None:
        node_definitions = DEFAULT_CROSSING_DEFINITIONS
    if edge_definitions is None:
        edge_definitions = DEFAULT_ROAD_DEFINITIONS

    # copy base graph (so we don't accidentally modify it)
    base_graph = deepcopy(base_graph)

    # standardize crossings
    df_nodes = pd.DataFrame.from_dict(dict(base_graph.nodes(data=True)), orient="index")
    df_nodes["std_crossing"] = _standardize_crossing_types(df_nodes)
    nx.set_node_attributes(
        base_graph, df_nodes["std_crossing"].to_dict(), name="crossing"
    )
    del df_nodes

    # assign distance to closest traffic signal (up to a maximum distance)
    max_dist = max(unsafe_distance_from_signal, fix_marked_crossings_near_signals or 0)
    for u in iter(base_graph.nodes):
        _update_traffic_signals(
            base_graph,
            u,
            max_dist=max_dist,
            edge_definitions=edge_definitions,
        )

    gdf_nodes, gdf_edges = ox.graph_to_gdfs(base_graph)
    gdf_nodes["safety"] = _set_node_safety(gdf_nodes, node_definitions)
    gdf_edges["crossable"] = _set_edge_crossable(gdf_edges, edge_definitions["major"])
    gdf_edges["angle"] = gdf_edges.apply(_get_angle, axis=1)
    gdf_edges["walkable"] = gdf_edges.apply(_get_walkable, axis=1)

    # nodes that are near traffic signals and aren't explicitly safe are
    # deemed unsafe
    gdf_nodes.loc[
        (gdf_nodes["traffic_signal_distance"] <= unsafe_distance_from_signal)
        & (gdf_nodes["safety"] != "safe"),
        "safety",
    ] = "unsafe"

    # add attribute to identify edges that are on the outside border of the
    # study area (no intermediate nodes will be generated between two edges on
    # the boundary)
    _, edges_undirected = ox.graph_to_gdfs(base_graph.to_undirected())
    poly = polygonize(edges_undirected.geometry.union_all())
    poly_gdf = gpd.GeoDataFrame(list(poly), crs=4326)
    poly_boundary = poly_gdf.union_all().boundary
    gdf_edges["in_boundary"] = gdf_edges.overlaps(poly_boundary) | gdf_edges.within(
        poly_boundary
    )

    # TODO rest of function (generating intermediate nodes, connecting them
    # according to the previously established rules, cleaning up the resulting
    # GeoDataFrames and returning a graph)
