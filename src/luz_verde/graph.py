"""
Functions used to generate the crossing graph from an OSMnx street network
graph.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypedDict

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely import LineString
from tqdm.auto import tqdm


class EdgeDefinitions(TypedDict):
    major: list[str]
    minor: list[str]


class NodeDefinitions(TypedDict):
    safe: list[str]
    unsafe: list[str]


class IntermediateNodeData(TypedDict): ...


class IntermediateEdgeData(TypedDict): ...


DEFAULT_ROAD_DEFINITIONS: NodeDefinitions = {
    "major": ["motorway", "trunk", "primary", "secondary", "tertiary", "busway"],
    "minor": ["residential", "unclassified", "cycleway"],
    "pedestrian": ["footway", "pedestrian", "path", "steps", "corridor"],
}

DEFAULT_CROSSING_DEFINITIONS: EdgeDefinitions = {
    "safe": ["traffic_signals", "uncontrolled", "unmarked"],
    "unsafe": ["no"],
}


def get_base_graph(
    loader_args,
    loader_fn: Callable[..., nx.MultiDiGraph] = ox.graph_from_polygon,
    cache_dir: Path = "cache",
) -> nx.MultiDiGraph:
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

    Returns
    ---
    base_graph : MultiDiGraph
        Base network graph, with all streets and crossings. All edges are
        bidirectional.
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

    # add attribute for nodes that are missing edges in their intersections
    # (because those edges fall outside the polygon). it's important to set
    # this flag before simplifying the graph
    out_degree = dict(street_graph.out_degree())
    nx.set_node_attributes(
        street_graph,
        {
            n: True
            for n, data in street_graph.nodes(data=True)
            if out_degree[n] != data.get("street_count")
        },
        name="is_missing_edges",
    )

    street_graph = ox.simplify_graph(
        street_graph,
        edge_attrs_differ=["osmid"],
        node_attrs_include=["highway", "is_missing_edges"],
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
        "no": (df_nodes["crossing"].isin(["no", "separate"])),
    }

    return np.select(conditions.values(), conditions.keys(), "none")


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
        case (True, True):
            return "neither"
        case (True, False):
            return "right"
        case (False, True):
            return "left"
        case _:
            return "both"


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
        node: gdf_nodes["crossing"].isin(matches)
        for node, matches in node_definitions.items()
    }

    return np.select(conditions.values(), conditions.keys(), "neutral")


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


def _generate_intermediate_nodes(
    gdf_nodes: gpd.GeoDataFrame, gdf_edges: gpd.GeoDataFrame, show_progress: bool
) -> gpd.GeoDataFrame:
    """
    For every node `u` in `gdf_nodes`, generates an intermediate node between
    each pair of adjacent outgoing edges from `u`, saving the edges and the
    intermediate angle between them as node attributes.

    Parameters
    ---
    gdf_nodes : GeoDataFrame
        The nodes of the street network.
    gdf_edges : GeoDataFrame
        The edges of the street network.
    show_progress : bool
        Whether to show a progress bar (using `tqdm`).

    Returns
    ---
    intermediate_nodes : GeoDataFrame
        GeoDataFrame containing the attributes and geometry of each
        intermediate node.
    """

    records = []
    groupby = gdf_edges.reset_index().groupby("u")
    iterator = tqdm(groupby) if show_progress else groupby
    for u, group in iterator:
        # skip node if it's on the edge
        if not pd.isna(gdf_nodes.loc[u, "is_missing_edges"]):
            continue

        edges = group.sort_values("angle", ascending=False).reset_index(drop=True)
        n = len(edges)

        for i in range(n):
            left, right = edges.iloc[i], edges.iloc[(i + 1) % n]
            # calculate intermediate angle
            angle_diff = (left.angle - right.angle) % (2 * np.pi)
            if angle_diff == 0:
                # edge case for one-way streets
                angle_mid = left.angle + np.pi
            else:
                angle_mid = left.angle - angle_diff / 2
            records.append(
                {
                    "base_node": u,  # this is also u_left and u_right
                    "v_left": left.v,
                    "key_left": left.key,
                    "v_right": right.v,
                    "key_right": right.key,
                    "angle": angle_mid,
                }
            )

    new_nodes_df = pd.DataFrame(records)
    return (
        gpd.GeoDataFrame(
            new_nodes_df,
            geometry=gdf_nodes.loc[new_nodes_df["base_node"]].geometry.values,
            crs=gdf_nodes.crs,
        )
        .rename_axis("osmid")
        .assign(
            x=lambda gdf: gdf.geometry.x,
            y=lambda gdf: gdf.geometry.y,
        )
    )


def _join_nodes(
    origin_nodes: gpd.GeoDataFrame,
    destination_nodes: gpd.GeoDataFrame,
    join_left: list[str],
    join_right: list[str],
    join_type: str,
    side: str,
) -> gpd.GeoDataFrame:
    """
    Merge nodes that share an edge on a given side. Keeps edge identifiers
    from the origin nodes.

    `origin_nodes` must have an `origin` column (or axis), `destination_nodes`
    must have a `destination` column (or axis), and `join_left` and
    `join_right` must not share values in common.
    """

    edge_cols = ["u_base", "v_base", "key_base"]

    return (
        origin_nodes[["origin", *join_left]]
        .merge(
            destination_nodes[["destination", *join_right]],
            left_on=join_left,
            right_on=join_right,
            how="inner",
        )[["origin", "destination", *join_left]]
        .rename(columns=dict(zip(join_left, edge_cols)))
        .assign(type=join_type, side=side)
    )


def _add_edge_data_to_joined_nodes(
    joined_nodes: pd.DataFrame, gdf_edges: gpd.GeoDataFrame, columns: list[str]
) -> gpd.GeoDataFrame:
    """
    Merges joined nodes with data from `gdf_edges` and returns a GeoDataFrame.
    """

    return gpd.GeoDataFrame(
        (
            joined_nodes.merge(
                gdf_edges[columns],
                left_on=["u_base", "v_base", "key_base"],
                right_index=True,
            )
        ),
        geometry="geometry",
        crs=gdf_edges.crs,
    )


def generate_crossing_graph(
    base_graph: nx.MultiDiGraph,
    node_definitions: NodeDefinitions | None = None,
    edge_definitions: EdgeDefinitions | None = None,
    unsafe_distance_from_signal: float = 20,
    fix_marked_crossings_near_signals: float = 0,
    verbose: bool = False,
) -> nx.MultiDiGraph:
    """
    From a base street network, generates a "crossing graph". Nodes in this
    graph show all possible places in an intersection where a pedestrian can
    safely stand, and edges between these nodes show how a pedestrian might
    cross or walk along a street (when able).

    Parameters
    ---
    base_graph : MultiDiGraph
        Original street network; generated with `get_base_graph`.
    node_definitions : NodeDefinitions
        Dictionary defining which crossing types are considered "safe" (can
        always cross) and "unsafe" (can never cross). Other crossing types are
        considered "neutral" (can only cross if the road is not major).

        The dictionary has two keys: `safe` and `unsafe`, and each value is a
        list of all crossing types belonging to that category. Possible
        crossing types are:
            - `traffic_signals`
            - `uncontrolled` (zebra crossings or other crossings where
              pedestrians always have preference)
            - `unmarked` (no explicit markings on the road but crossing is
              allowed)
            - `none` (no markings, pedestrians may cross but don't have the
              right of way)
            - `no` (no crossing allowed under any circumstances)

        By default, `traffic_signals`, `uncontrolled` and `unmarked` are
        considered safe, while `no` is considered unsafe.
    edge_definitions : EdgeDefinitions
        Dictionary defining which highway types are considered "major" (can
        only cross if the node you are crossing along is safe) and "minor"
        (can always cross unless the node you are crossing along is unsafe).
        Highway types that are neither major nor minor follow the same
        behavior as minor roads, but they don't allow propagation of unsafe
        crossings near traffic signals (see the `unsafe_distance_from_signal`
        parameter for more information); this last category should be reserved
        for roads that don't allow any high-speed traffic (e.g. pedestrian
        roads, service roads).

        The dictionary has two keys: `major` and `minor`, and each value is a
        list of regex patterns; if a highway type matches with any of the
        regex patterns in the list, it will be considered of that group (e.g.
        `"major": ["primary"]` will consider both `primary` and `primary_link`
        as major roads).

        By default, major roads contain `motorway`, `trunk`, `primary`,
        `secondary`, `tertiary` or `busway`, and minor roads contain
        `residential`, `unclassified` or `cycleway`.
    unsafe_distance_from_signal : float, default: 20
        Crossings that are at most this distance away from a traffic signal
        will be deemed unsafe, unless they were previously deemed safe. This
        is to avoid routing that might make people cross along a road when
        there is a proper traffic signal crossing nearby. Distance is measured
        along the network, considering only major and minor roads.
    fix_marked_crossings_near_signals : float, default: 0
        Crossings tagged `crossing=marked` in OSM will be converted to
        `crossing=traffic_signals` if they are at most this distance away from
        a traffic signal. This is a quick fix for a common issue in Santiago's
        OSM data (where traffic signal crossings are incorrectly tagged as
        "marked", a type of uncontrolled crossing). The distance is measured
        along the network, considering only major and minor roads.
    verbose : bool, default: False
        Whether to print updates and show progress bars while the graph is
        being generated.

    Returns
    ---
    crossing_graph : MultiDiGraph
        The generated crossing graph.
    """

    if node_definitions is None:
        node_definitions = DEFAULT_CROSSING_DEFINITIONS
    if edge_definitions is None:
        edge_definitions = DEFAULT_ROAD_DEFINITIONS

    if verbose:
        print("(1/3) Calculating auxiliary variables...")

    # copy base graph (so we don't accidentally modify it)
    base_graph = base_graph.copy()

    df_nodes = pd.DataFrame.from_dict(dict(base_graph.nodes(data=True)), orient="index")

    # before standardizing, set marked crossings if we want to fix them later
    if fix_marked_crossings_near_signals > 0:
        nx.set_node_attributes(
            base_graph, (df_nodes["crossing"] == "marked").to_dict(), name="is_marked"
        )

    # standardize crossings
    df_nodes["std_crossing"] = _standardize_crossing_types(df_nodes)
    nx.set_node_attributes(
        base_graph, df_nodes["std_crossing"].to_dict(), name="crossing"
    )
    del df_nodes

    # assign distance to closest traffic signal (up to a maximum distance)
    max_dist = max(unsafe_distance_from_signal, fix_marked_crossings_near_signals)
    for u in iter(base_graph.nodes):
        _update_traffic_signals(
            base_graph,
            u,
            max_dist=max_dist,
            edge_definitions=edge_definitions,
        )

    gdf_nodes, gdf_edges = ox.graph_to_gdfs(base_graph)
    del base_graph
    gdf_nodes["safety"] = _set_node_safety(gdf_nodes, node_definitions)
    gdf_edges["crossable"] = _set_edge_crossable(gdf_edges, edge_definitions["major"])
    gdf_edges["angle"] = gdf_edges.geometry.apply(_get_angle)
    gdf_edges["walkable"] = gdf_edges.apply(_get_walkable, axis=1)

    # nodes that are near traffic signals and aren't explicitly safe are
    # deemed unsafe
    gdf_nodes.loc[
        (gdf_nodes["traffic_signal_distance"] <= unsafe_distance_from_signal)
        & (gdf_nodes["safety"] != "safe"),
        "safety",
    ] = "unsafe"

    # if necessary, fix marked crossings too close to traffic signals
    if fix_marked_crossings_near_signals > 0:
        gdf_nodes.loc[
            (gdf_nodes["traffic_signal_distance"] <= fix_marked_crossings_near_signals)
            & gdf_nodes["is_marked"],
            "crossing",
        ] = "traffic_signals"
        gdf_nodes = gdf_nodes.drop(columns="is_marked")

    # generate intermediate nodes
    if verbose:
        print("(2/3) Generating intermediate nodes...")
    intermediate_nodes = _generate_intermediate_nodes(
        gdf_nodes, gdf_edges, show_progress=verbose
    )

    # GENERATE CONNECTIONS BETWEEN INTERMEDIATE NODES
    if verbose:
        print("(3/3) Generating intermediate edges...")
    origin_nodes = intermediate_nodes.rename_axis("origin").reset_index()
    destination_nodes = (
        intermediate_nodes.rename_axis("destination")
        .reset_index()
        .rename(columns={"base_node": "base_node_dest"})
    )

    # 1. walking along an edge
    right_side_walks = _join_nodes(
        origin_nodes,
        destination_nodes,
        join_left=["base_node", "v_left", "key_left"],
        join_right=["v_right", "base_node_dest", "key_right"],
        join_type="walk",
        side="right",
    )
    left_side_walks = _join_nodes(
        origin_nodes,
        destination_nodes,
        join_left=["base_node", "v_right", "key_right"],
        join_right=["v_left", "base_node_dest", "key_left"],
        join_type="walk",
        side="left",
    )
    all_walks = pd.concat([right_side_walks, left_side_walks], ignore_index=True)
    del left_side_walks, right_side_walks
    all_walks = _add_edge_data_to_joined_nodes(
        all_walks,
        gdf_edges,
        columns=[
            "osmid",
            "highway",
            "name",
            "length",
            "crossable",
            "walkable",
            "geometry",
        ],
    )
    # the side we are walking on must be deemed walkable
    all_walks = all_walks[
        (all_walks["walkable"] == "both") | (all_walks["walkable"] == all_walks["side"])
    ]

    # 2. crossing an edge along a base node
    cw_crosses = _join_nodes(
        origin_nodes,
        destination_nodes,
        join_left=["base_node", "v_right", "key_right"],
        join_right=["base_node_dest", "v_left", "key_left"],
        join_type="cross",
        side="clockwise",
    )
    ccw_crosses = _join_nodes(
        origin_nodes,
        destination_nodes,
        join_left=["base_node", "v_left", "key_left"],
        join_right=["base_node_dest", "v_right", "key_right"],
        join_type="cross",
        side="counterclockwise",
    )
    all_crosses = pd.concat([cw_crosses, ccw_crosses], ignore_index=True)
    del cw_crosses, ccw_crosses
    # add base node data (we will need it to determine which crossing spots
    # are valid)
    all_crosses = all_crosses.merge(
        gdf_nodes[["crossing", "safety", "geometry"]],
        left_on="u_base",
        right_index=True,
    ).assign(length=0.1)
    all_crosses = _add_edge_data_to_joined_nodes(
        all_crosses,
        gdf_edges,
        columns=[
            "osmid",
            "highway",
            "name",
            "crossable",
            "walkable",
        ],
    )
    # if the edge is crossable, then we can cross unless the node is unsafe;
    # if the edge is uncrossable, then we can only cross if the node is safe
    all_crosses = all_crosses[
        (all_crosses["safety"] == "safe")
        | (all_crosses["crossable"] & (all_crosses["safety"] != "unsafe"))
    ]

    # consolidate intermediate edges (walks + crosses)
    intermediate_edges = (
        gpd.GeoDataFrame(
            data=pd.concat([all_walks, all_crosses], ignore_index=True),
            geometry="geometry",
            crs=all_crosses.crs,
        )
        # rename columns and add a new "key" for duplicate u, v pairs (so that
        # osmnx accepts this graph as valid)
        .rename(
            columns={
                "origin": "u",
                "destination": "v",
                "osmid": "edge_osmid",
                "highway": "edge_highway",
                "name": "edge_name",
                "crossable": "edge_crossable",
                "walkable": "edge_walkable",
                "crossing": "node_crossing",
                "safety": "node_safety",
            }
        )
        .assign(key=lambda gdf: gdf.groupby(["u", "v"]).cumcount())
        .set_index(["u", "v", "key"])
    )
    del all_walks, all_crosses

    # drop edges where origin and destination are the same (useless!)
    intermediate_edges = intermediate_edges[
        intermediate_edges.index.get_level_values(0)
        != intermediate_edges.index.get_level_values(1)
    ]

    # generate new graph and only keep largest component
    new_graph = ox.graph_from_gdfs(intermediate_nodes, intermediate_edges)
    new_graph = ox.truncate.largest_component(new_graph, strongly=False)

    if verbose:
        print("Graph generated.")

    return new_graph
