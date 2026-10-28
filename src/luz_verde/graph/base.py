"""
Functions used to generate a "base graph" from an OSMnx street network graph.
"""

import warnings
from collections.abc import Callable
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

from ..common import (
    DEFAULT_CROSSING_DEFINITIONS,
    DEFAULT_ROAD_DEFINITIONS,
    EdgeDefinitions,
    NodeDefinitions,
    match_road,
)


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
            | (
                df_nodes["crossing:markings"].notna()
                & (df_nodes["crossing:markings"] != "no")
            )
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
            if match_road(
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

    ox.settings.useful_tags_node = [
        "highway",
        "crossing",
        "crossing:signals",
        "crossing:markings",
    ]
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

    # standardize crossings (clear all values beforehand and only assign
    # non-null ones)
    df_nodes["std_crossing"] = _standardize_crossing_types(df_nodes)
    for _, d in street_graph.nodes(data=True):
        d.pop("crossing", None)
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
                        <= fix_marked_crossings_near_signals
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
