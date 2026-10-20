"""
Functions used to generate the crossing graph from an OSMnx street network
graph.
"""

import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely import LineString, Point
from tqdm.auto import tqdm


class EdgeDefinitions(TypedDict):
    major: list[str]
    minor: list[str]
    pedestrian: list[str]


class NodeDefinitions(TypedDict):
    safe: list[str]
    unsafe: list[str]


@dataclass
class _IntermediateNodeRecord:
    index: int
    base_node: int
    angle: float = np.nan


@dataclass
class _CcwCrossingRecord:
    u: int
    v: int
    edge_osmid: int
    edge_highway: str
    edge_name: str
    edge_crossable: bool
    edge_walkable: str
    node_crossing: str
    node_safety: str
    geometry: Point
    type: str = "cross"
    side: str = "counterclockwise"


DEFAULT_ROAD_DEFINITIONS: NodeDefinitions = {
    "major": ["motorway", "trunk", "primary", "secondary", "tertiary", "busway"],
    "minor": ["residential", "unclassified"],
    "pedestrian": ["footway", "pedestrian", "path", "steps", "corridor"],
}

DEFAULT_CROSSING_DEFINITIONS: EdgeDefinitions = {
    "safe": ["traffic_signals", "uncontrolled", "unmarked"],
    "unsafe": ["no"],
}

DEFAULT_UNWALKABLE_PATS = ["motorway", "trunk", "busway", "cycleway"]


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
            | (df_nodes["crossing:signals"] == "yes")
        ),
        "uncontrolled": (
            df_nodes["crossing"].isin(["uncontrolled", "marked", "zebra"])
        ),
        "unmarked": (df_nodes["crossing"].str.contains("unmarked")),
        "no": (df_nodes["crossing"].isin(["no", "separate"])),
    }

    return np.select(conditions.values(), conditions.keys(), None)


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

    # if the node has traffic signals, its distance is 0
    if (
        G.nodes[u].get("crossing") == "traffic_signals"
        or G.nodes[u].get("highway") == "traffic_signals"
    ):
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


def get_base_graph(
    loader_args,
    loader_fn: Callable[..., nx.MultiDiGraph] = ox.graph_from_polygon,
    node_definitions: NodeDefinitions | None = None,
    edge_definitions: EdgeDefinitions | None = None,
    unsafe_distance_from_signal: float = 20,
    fix_marked_crossings_near_signals: float = 0,
    cache_dir: Path = "cache",
    verbose: bool = False,
) -> nx.MultiDiGraph:
    """
    Uses OSMnx to download the base graph with all public streets and
    crossings for a certain region. Also does the following preprocessing
    steps:

    - Intersections that are too close to traffic signals are marked as
      uncrossable, unless they explicitly allow crossing (this is to "force"
      the routing along designated crossing places where possible).
    - If requested, crossings tagged as "marked" are changed to
      "traffic_signals" if they are close to one.
    - The graph is simplified by OSMnx, keeping all nodes with pedestrian
      crossings and nodes along the "boundary" of the study area (where there
      is at least one outgoing edge not present in the graph).

    Parameters
    ---
    loader_args
        Positional arguments to be passed to `loader_fn`. If it is a tuple,
        then each element of the tuple is passed as a separate positional
        argument to `loader_fn`.
    loader_fn : callable, default: osmnx.graph_from_polygon
        OSMnx loader function. Must work when passed `loader_args` as
        positional arguments.
    node_definitions : NodeDefinitions or None, default: None
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
            - `no` (no crossing allowed under any circumstances)
            - `None` (the object, not a string!), for other intersections
              where crossing is neither allowed nor disallowed

        By default, `traffic_signals`, `uncontrolled` and `unmarked` are
        considered safe, while `no` is considered unsafe.

        IMPORTANT: `no` will always be considered unsafe, even if it is not
        explicitly in the unsafe list.
    edge_definitions : EdgeDefinitions or None, default: None
        Dictionary defining which highway types are considered "major" (can
        only cross if the node you are crossing along is safe), "minor" (can
        always cross unless the node you are crossing along is unsafe) and
        "pedestrian" (can always walk on and cross).

        Highway types that are in none of the above categories follow the same
        behavior as minor roads, but they don't allow propagation of unsafe
        crossings near traffic signals (see the `unsafe_distance_from_signal`
        parameter for more information); this last category should be reserved
        for roads that don't allow any high-speed traffic (e.g. service roads,
        living roads).

        The dictionary has three keys: `major`, `minor`, and `pedestrian` and
        each value is a list of regex patterns; if a highway type matches with
        any of the regex patterns in the list, it will be considered of that
        group (e.g. `"major": ["primary"]` will consider both `primary` and
        `primary_link` as major roads).

        By default, major roads contain `motorway`, `trunk`, `primary`,
        `secondary`, `tertiary` or `busway`, minor roads contain `residential`
        or `unclassified`, and pedestrian roads contain `footway`,
        `pedestrian`, `path`, `steps` or `corridor`.
    unsafe_distance_from_signal : float, default: 20
        Crossings that are at most this distance away (in meters) from a
        traffic signal will be deemed unsafe, unless they were previously
        deemed safe. This is to avoid routing that might make people cross
        along a road when there is a proper traffic signal crossing nearby.
        Distance is measured along the network, considering only major and
        minor roads.
    fix_marked_crossings_near_signals : float, default: 0
        Crossings tagged `crossing=marked` in OSM will be converted to
        `crossing=traffic_signals` if they are at most this distance away from
        a traffic signal. This is a quick fix for a common issue in Santiago's
        OSM data (where traffic signal crossings are incorrectly tagged as
        "marked", a type of uncontrolled crossing). The distance is measured
        (in meters) along the network, considering only major and minor roads.
    cache_dir : Path, default: "cache"
        Path to save/load cached OSMnx downloads.
    verbose : bool, default: False
        Whether to print updates while the graph is being loaded.

    Returns
    ---
    base_graph : MultiDiGraph
        Base network graph, with all streets and crossings. All edges are
        bidirectional.
    """

    if node_definitions is None:
        node_definitions = DEFAULT_CROSSING_DEFINITIONS
    if edge_definitions is None:
        edge_definitions = DEFAULT_ROAD_DEFINITIONS

    if "no" in node_definitions["safe"]:
        warnings.warn("Can't have `crossing=no` as safe, removing.")
        node_definitions["unsafe"].remove("no")
    if "no" not in node_definitions["unsafe"]:
        node_definitions["unsafe"].append("no")

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
    ox.settings.bidirectional_network_types.append("all_public")

    if not isinstance(loader_args, tuple):
        loader_args = (loader_args,)

    if verbose:
        print("(1/4) Downloading street network...")

    street_graph = loader_fn(*loader_args, network_type="all_public", simplify=False)

    if verbose:
        print("(2/4) Adding pre-simplification node attributes...")

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

    df_nodes = pd.DataFrame.from_dict(
        dict(street_graph.nodes(data=True)), orient="index"
    )

    # before standardizing, set marked crossings if we want to fix them later
    if fix_marked_crossings_near_signals > 0:
        nx.set_node_attributes(
            street_graph, (df_nodes["crossing"] == "marked").to_dict(), name="is_marked"
        )

    # standardize crossings
    df_nodes["std_crossing"] = _standardize_crossing_types(df_nodes)
    nx.set_node_attributes(
        street_graph, df_nodes["std_crossing"].dropna().to_dict(), name="crossing"
    )
    del df_nodes

    # assign distance to closest traffic signal (up to a maximum distance)
    max_dist = max(unsafe_distance_from_signal, fix_marked_crossings_near_signals)
    for u in iter(street_graph.nodes):
        _update_traffic_signals(
            street_graph,
            u,
            max_dist=max_dist,
            edge_definitions=edge_definitions,
        )
    if verbose:
        print("(3/4) Simplifying graph...")

    street_graph = ox.simplify_graph(
        street_graph,
        edge_attrs_differ=["osmid"],
        node_attrs_include=["crossing", "is_missing_edges"],
    )

    if verbose:
        print("(4/4) Adding post-simplification node attributes...")

    # assign crossing=no to nodes that are too close to a traffic signal
    nx.set_node_attributes(
        street_graph,
        {
            n: {"crossing": "no"}
            for n, data in street_graph.nodes(data=True)
            if (
                data.get("traffic_signal_distance", np.inf)
                <= unsafe_distance_from_signal
            )
            and (data.get("crossing") not in node_definitions["safe"])
        },
    )

    # if necessary, fix marked crossings too close to traffic signals
    if fix_marked_crossings_near_signals > 0:
        nx.set_node_attributes(
            street_graph,
            {
                n: {"crossing": "traffic_signals"}
                for n, data in street_graph.nodes(data=True)
                if (
                    (
                        data.get("traffic_signal_distance", np.inf)
                        <= unsafe_distance_from_signal
                    )
                    and data.get("is_marked")
                )
            },
        )

    street_graph.graph["node_definitions"] = node_definitions
    street_graph.graph["edge_definitions"] = edge_definitions

    if verbose:
        print(
            "Graph loaded, "
            f"{street_graph.number_of_nodes()} nodes, "
            f"{street_graph.number_of_edges()} edges."
        )

    return street_graph


def _get_angle(geometry: LineString) -> float:
    """
    Gets the angle for a certain LineString object, calculated from its first
    two points, with respect to a horizontal east-west line that crosses the
    first point.
    """

    p0 = geometry.coords[0]
    p1 = geometry.coords[1]

    return np.arctan2(p1[1] - p0[1], p1[0] - p0[0])


def _get_walkable(
    edge: pd.Series, pedestrian_pats: list[str], default_unwalkable_pats: list[str]
) -> Literal["both", "left", "right", "neither"]:
    """
    Returns which sides an edge/road is walkable along. Can be either "both",
    "left", "right" or "neither".

    The side is calculated from the perspective starting at the first point in
    the edge, and ending at the last point in the edge (e.g. if the left side
    is walkable, you can walk along the edge while always being to the left of
    said edge if you were looking forward while walking).

    If the edge's `highway` tag matches with any of the regex patterns in
    `default_unwalkable_pats`, we will assume it is unwalkable unless
    explicitly stated otherwise. Else, we will assume all sides are walkable
    unless shown otherwise.
    """

    foot = edge["foot"]
    sidewalk = edge["sidewalk"]

    if _match_road(edge["highway"], pedestrian_pats) or foot in ("yes", "designated"):
        return "both"
    if foot in ("no", "use_sidepath"):
        return "neither"

    def _sides(left: bool, right: bool) -> Literal["both", "left", "right", "neither"]:
        """Given whether the left and right sides are walkable, returns a
        string for where you can walk on."""
        if edge["reversed"]:
            left, right = right, left
        return {
            (True, True): "both",
            (True, False): "left",
            (False, True): "right",
            (False, False): "neither",
        }[left, right]

    if _match_road(edge["highway"], default_unwalkable_pats):
        base = sidewalk in ("yes", "both") or edge["sidewalk:both"] == "yes"
        return _sides(
            left=base or edge["sidewalk:left"] == "yes" or sidewalk == "left",
            right=base or edge["sidewalk:right"] == "yes" or sidewalk == "right",
        )

    # if the edge is not in `default_unwalkable_pats`
    base = sidewalk in ("no", "separate") or edge["sidewalk:both"] in ("no", "separate")
    unwalkable_left = (
        base or edge["sidewalk:left"] in ("no", "separate") or sidewalk == "right"
    )
    unwalkable_right = (
        base or edge["sidewalk:right"] in ("no", "separate") or sidewalk == "left"
    )
    return _sides(left=not unwalkable_left, right=not unwalkable_right)


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


def _is_crossing_allowed(u_data: pd.Series, right_edge: pd.Series) -> bool:
    """Returns whether you can cross `right_edge` along `u_data`."""
    return (u_data["safety"] == "safe") or (
        right_edge.crossable and u_data["safety"] != "unsafe"
    )


def _make_crossing(
    u_node: int,
    v_node: int,
    right_edge: pd.Series,
    u_data: pd.Series,
) -> _CcwCrossingRecord:
    return _CcwCrossingRecord(
        u=u_node,
        v=v_node,
        edge_osmid=right_edge.osmid,
        edge_highway=right_edge.highway,
        edge_name=right_edge["name"],
        edge_crossable=right_edge.crossable,
        edge_walkable=right_edge.walkable,
        node_crossing=u_data["crossing"],
        node_safety=u_data["safety"],
        geometry=u_data["geometry"],
    )


def _midpoint_angle(left_angle: float, right_angle: float) -> float:
    """Returns the angle bisector between two edge angles
    (counterclockwise)."""
    angle_diff = (left_angle - right_angle) % (2 * np.pi)
    if angle_diff == 0:
        # edge case for one-way streets
        return left_angle + np.pi
    return left_angle - angle_diff / 2


def _generate_intermediate_nodes_and_crossings(
    gdf_nodes: gpd.GeoDataFrame,
    gdf_edges: gpd.GeoDataFrame,
    show_progress: bool,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    For every node `u` in `gdf_nodes`, generates intermediate nodes in the
    places where a pedestrian could stand (either on a pedestrian way, or
    between two non-pedestrian ways). Connects these new intermediate nodes
    with crossings between them (where allowed), and also sets the
    intermediate nodes to the left and right of each way (modifying the
    `left_node` and `right_node` attributes of `gdf_edges`).

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
    crossings : GeoDataFrame
        GeoDataFrame containing the attributes and geometry of each crossing.
    """

    node_records: list[_IntermediateNodeRecord] = []
    crossing_records: list[_CcwCrossingRecord] = []
    idx = 0

    groupby = gdf_edges.groupby("u")
    iterator = tqdm(groupby) if show_progress else groupby

    for u, group in iterator:
        u_data = gdf_nodes.loc[u]

        # skip node if it's on the boundary
        if not pd.isna(u_data["is_missing_edges"]):
            continue

        # if it's all pedestrian roads and the crossing has no specific tag,
        # then simplify to just one intermediate node
        if group["is_pedestrian"].all() and pd.isna(u_data["crossing"]):
            node_records.append(_IntermediateNodeRecord(index=idx, base_node=u))
            gdf_edges.loc[group.index, ["left_node", "right_node"]] = idx
            idx += 1
            continue

        edges = group.sort_values("angle")
        n = len(edges)
        previous_node: int = -1
        crossing_with_missing_u: _CcwCrossingRecord | None = None
        edge_with_missing_right_walk: tuple[int, int, int] | None = None

        for i in range(n):
            right, left = edges.iloc[i], edges.iloc[(i + 1) % n]

            if left.is_pedestrian:
                # place a node ON the pedestrian road
                node_records.append(
                    _IntermediateNodeRecord(index=idx, base_node=u, angle=left.angle)
                )

                # cross over the right-side road (if allowed)
                if _is_crossing_allowed(u_data, right):
                    new_crossing = _make_crossing(previous_node, idx, right, u_data)
                    crossing_records.append(new_crossing)
                    if previous_node == -1:
                        crossing_with_missing_u = new_crossing

                # this is actually a "center-walk" (because the left edge is
                # pedestrian, we walk ON it not next to it), but we save it in
                # the right and left nodes to save a column
                gdf_edges.loc[left.name, ["left_node", "right_node"]] = idx

                if (not right.is_pedestrian) and (right.walkable in ("left", "both")):
                    gdf_edges.loc[right.name, "left_node"] = idx

                previous_node = idx
                idx += 1

            elif right.is_pedestrian:
                # skip intermediate node, simply connect the existing ones
                if left.walkable in ("right", "both"):
                    gdf_edges.loc[left.name, "right_node"] = previous_node
                    if previous_node == -1:
                        edge_with_missing_right_walk = left.name

            else:
                # both edges are non-pedestrian: add intermediate node between
                # them
                node_records.append(
                    _IntermediateNodeRecord(
                        index=idx,
                        base_node=u,
                        angle=_midpoint_angle(left.angle, right.angle),
                    )
                )
                if _is_crossing_allowed(u_data, right) and left.name != right.name:
                    new_crossing = _make_crossing(previous_node, idx, right, u_data)
                    crossing_records.append(new_crossing)
                    if previous_node == -1:
                        crossing_with_missing_u = new_crossing
                if left.walkable in ("right", "both"):
                    gdf_edges.loc[left.name, "right_node"] = idx
                if right.walkable in ("left", "both"):
                    gdf_edges.loc[right.name, "left_node"] = idx
                previous_node = idx
                idx += 1

        # final fixes (closing the loop)
        if crossing_with_missing_u is not None:
            crossing_with_missing_u.u = previous_node
        if edge_with_missing_right_walk is not None:
            gdf_edges.loc[edge_with_missing_right_walk, "right_node"] = previous_node

    return _build_node_gdf(node_records, gdf_nodes), _build_crossing_gdf(
        crossing_records, gdf_nodes
    )


def _build_node_gdf(
    node_records: list[_IntermediateNodeRecord],
    gdf_nodes: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    new_nodes_df = pd.DataFrame(vars(r) for r in node_records)
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


def _build_crossing_gdf(
    crossing_records: list[_CcwCrossingRecord],
    gdf_nodes: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    crossings_df = pd.DataFrame(vars(r) for r in crossing_records)
    return (
        gpd.GeoDataFrame(
            crossings_df,
            geometry="geometry",
            crs=gdf_nodes.crs,
        )
    ).assign(length=0.1)


def generate_crossing_graph(
    base_graph: nx.MultiDiGraph,
    node_definitions: NodeDefinitions | None = None,
    edge_definitions: EdgeDefinitions | None = None,
    default_unwalkable_pats: list[str] | None = None,
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
    node_definitions : NodeDefinitions or None, default: None
        Dictionary defining which crossing types are considered "safe" (can
        always cross) and "unsafe" (can never cross). Other crossing types are
        considered "neutral" (can only cross if the road is not major).

        See `get_base_graph` for more information.

        By default, uses the node definitions given to the base graph when it
        was generated.
    edge_definitions : EdgeDefinitions or None, default: None
        Dictionary defining which highway types are considered "major" (can
        only cross if the node you are crossing along is safe), "minor" (can
        always cross unless the node you are crossing along is unsafe) and
        "pedestrian" (can always walk on and cross).

        See `get_base_graph` for more information.

        By default, uses the edge definitions given to the base graph when it
        was generated.
    default_unwalkable_pats : list[str] or None, default: None
        By default, roads are assumed walkable on both sides unless shown
        otherwise. Roads whose `highway` tag matches to any of the regex
        patterns in `default_unwalkable_pats` are assumed to be unwalkable on
        both sides unless shown otherwise.
    verbose : bool, default: False
        Whether to print updates and show progress bars while the graph is
        being generated.

    Returns
    ---
    crossing_graph : MultiDiGraph
        The generated crossing graph.
    """

    if node_definitions is None:
        node_definitions = base_graph.graph.get("node_definitions")
        if node_definitions is None:
            warnings.warn(
                "No value for `node_definitions` was found in the base "
                "graph; using default value."
            )
            node_definitions = DEFAULT_CROSSING_DEFINITIONS
        node_definitions = base_graph.graph.get("node_definitions")
        if node_definitions is None:
            warnings.warn(
                "No value for `node_definitions` was found in the base "
                "graph; using default value."
            )
            node_definitions = DEFAULT_CROSSING_DEFINITIONS
    if edge_definitions is None:
        edge_definitions = base_graph.graph.get("edge_definitions")
        if edge_definitions is None:
            warnings.warn(
                "No value for `edge_definitions` was found in the base "
                "graph; using default value."
            )
            edge_definitions = DEFAULT_ROAD_DEFINITIONS

    if default_unwalkable_pats is None:
        default_unwalkable_pats = DEFAULT_UNWALKABLE_PATS

    if verbose:
        print("(1/3) Calculating auxiliary variables...")

    gdf_nodes, gdf_edges = ox.graph_to_gdfs(base_graph)
    gdf_nodes["safety"] = _set_node_safety(gdf_nodes, node_definitions)
    gdf_edges["crossable"] = _set_edge_crossable(gdf_edges, edge_definitions["major"])
    gdf_edges["angle"] = gdf_edges.geometry.apply(_get_angle)
    gdf_edges["walkable"] = gdf_edges.apply(
        _get_walkable,
        args=(edge_definitions["pedestrian"], default_unwalkable_pats),
        axis=1,
    )
    gdf_edges["is_pedestrian"] = gdf_edges["highway"].str.match(
        "|".join(edge_definitions["pedestrian"])
    )
    gdf_edges[["left_node", "right_node"]] = np.nan

    # generate intermediate nodes and crossings
    if verbose:
        print("(2/3) Generating intermediate nodes and crossings...")
    intermediate_nodes, ccw_crosses = _generate_intermediate_nodes_and_crossings(
        gdf_nodes, gdf_edges, show_progress=verbose
    )
    all_crosses = pd.concat(
        [
            ccw_crosses,
            ccw_crosses.rename(columns={"u": "v", "v": "u"}).assign(side="clockwise"),
        ]
    )
    all_crosses = all_crosses.drop_duplicates(subset=["u", "v"])
    del ccw_crosses

    # generate walks
    if verbose:
        print("(3/3) Generating walks along roads...")

    # right-side walks
    right_side_walks = (
        gdf_edges.loc[
            gdf_edges["right_node"].notna(),
            [
                "right_node",
                "osmid",
                "highway",
                "name",
                "crossable",
                "walkable",
                "geometry",
                "is_pedestrian",
                "length",
            ],
        ]
        .merge(
            gdf_edges.loc[gdf_edges["left_node"].notna(), ["left_node"]],
            left_on=["u", "v", "key"],
            right_on=["v", "u", "key"],
        )
        .rename(columns={"right_node": "u", "left_node": "v"})
    )
    right_side_walks[["type", "side"]] = ("walk", "right")

    # right-side walks along pedestrian roads are actually center walks
    right_side_walks.loc[right_side_walks["is_pedestrian"], "side"] = "center"

    # left-side walks
    left_side_walks = (
        gdf_edges.loc[
            ~gdf_edges["is_pedestrian"] & gdf_edges["left_node"].notna(),
            [
                "left_node",
                "osmid",
                "highway",
                "name",
                "crossable",
                "walkable",
                "geometry",
                "length",
            ],
        ]
        .merge(
            gdf_edges.loc[gdf_edges["right_node"].notna(), ["right_node"]],
            left_on=["u", "v", "key"],
            right_on=["v", "u", "key"],
        )
        .rename(columns={"left_node": "u", "right_node": "v"})
    )
    left_side_walks[["type", "side"]] = ("walk", "left")

    # concatenate left, center and right-side walks
    all_walks = pd.concat([right_side_walks, left_side_walks], ignore_index=True)
    all_walks[["u", "v"]] = all_walks[["u", "v"]].astype(int)
    all_walks = all_walks.rename(
        columns={
            "osmid": "edge_osmid",
            "highway": "edge_highway",
            "name": "edge_name",
            "crossable": "edge_crossable",
            "walkable": "edge_walkable",
        }
    ).drop(columns="is_pedestrian")
    del left_side_walks, right_side_walks

    # consolidate intermediate edges (walks + crosses)
    intermediate_edges = (
        gpd.GeoDataFrame(
            data=pd.concat([all_walks, all_crosses], ignore_index=True),
            geometry="geometry",
            crs=all_crosses.crs,
        )
        # add a new "key" for duplicate u, v pairs (so that osmnx accepts this
        # graph as valid)
        .assign(key=lambda gdf: gdf.groupby(["u", "v"]).cumcount())
        .set_index(["u", "v", "key"])
        .reindex(
            columns=[
                "type",
                "side",
                "edge_osmid",
                "edge_highway",
                "edge_name",
                "edge_crossable",
                "edge_walkable",
                "node_crossing",
                "node_safety",
                "length",
                "geometry",
            ]
        )
    )
    del all_walks, all_crosses

    # drop edges where origin and destination are the same. this happens in
    # two cases:
    # - walks along a looping pedestrian road (same starting and ending point)
    # - an edge case where an intersection only contains two ways: a
    #   pedestrian one and a non-pedestrian one. in this case, the algorithm
    #   generates a "crossing" over the non-pedestrian way, but both the
    #   origin and the destination are the intermediate node generated on the
    #   pedestrian way. we could filter out these cases as they are being
    #   generated, but honestly i think it's easier to do it here
    intermediate_edges = intermediate_edges[
        intermediate_edges.index.get_level_values(0)
        != intermediate_edges.index.get_level_values(1)
    ]

    # generate new graph and only keep largest component
    new_graph = ox.graph_from_gdfs(intermediate_nodes, intermediate_edges)
    new_graph = ox.truncate.largest_component(new_graph, strongly=False)

    if verbose:
        print(
            "Graph generated, "
            f"{new_graph.number_of_nodes()} nodes, "
            f"{new_graph.number_of_edges()} edges."
        )

    return new_graph
