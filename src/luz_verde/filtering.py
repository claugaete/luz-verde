"""
Functions for filtering edges from a crossing graph and getting its connected
components after filtering.
"""

from collections.abc import Callable
from typing import Literal

import networkx as nx
import osmnx as ox
import pandas as pd
from tqdm.auto import tqdm

from .common import EdgeDefinitions, IntermediateEdgeData, match_road


def _is_edge_traffic_signal(
    data: IntermediateEdgeData, edge_definitions: EdgeDefinitions
) -> bool:
    """Returns whether an edge corresponds to a traffic signal crossing."""

    return data.get("node_crossing") == "traffic_signals"


def _is_edge_unmarked_major(
    data: IntermediateEdgeData, edge_definitions: EdgeDefinitions
) -> bool:
    """Returns whether an edge corresponds to an unmarked crossing along a
    major road."""

    return data.get("node_crossing") == "unmarked" and match_road(
        data.get("edge_highway"), edge_definitions["major"]
    )


def _is_edge_stopping(
    data: IntermediateEdgeData, edge_definitions: EdgeDefinitions
) -> bool:
    """Returns whether a crossing requires stopping (traffic signals, or
    unmarked crossings along major roads)."""

    return _is_edge_traffic_signal(data, edge_definitions) or _is_edge_unmarked_major(
        data, edge_definitions
    )


def filter_edges(
    crossing_graph: nx.MultiDiGraph,
    filter_function: Literal["no_traffic_signals", "no_unmarked_major", "no_stopping"]
    | Callable[[IntermediateEdgeData, EdgeDefinitions], bool],
    return_dropped_edges: bool = False,
) -> nx.MultiDiGraph | tuple[nx.MultiDiGraph, list[tuple[int, int, int]]]:
    """
    Filters out edges in a crossing graph according to some function,
    returning a copy of the graph with the filtered edges dropped.

    Parameters
    ---
    crossing_graph : MultiDiGraph
        Crossing graph, generated with `generate_crossing_graph`.
    filter_function
        The function to use to filter out edges, only those that return False
        (not filtered) are kept.  Can be one of:

        - `no_traffic_signals`: removes all crossings involving traffic
          signals.
        - `no_unmarked_major`: removes all unmarked crossings along major
          roads (where the pedestrian is not usually allowed the right of
          way).
        - `no_stopping`: removes all crossings where a pedestrian would have
          to stop and wait for incoming cars (traffic signals, as well as
          unmarked crossings along major roads).
        - Any function that takes two arguments: a dictionary with edge data
          (see type `IntermediateEdgeData`), and a dictionary with major and
          minor road types (see type `EdgeDefinitions`); and returns a boolean
          indicating whether the edge is dropped (True) or kept (False).
    return_dropped_edges : bool, default: False
        Whether to return the list of dropped edges alongside the new graph.

    Returns
    ---
    filtered_graph : MultiDiGraph or tuple[MultiDiGraph, list[tuple]]
        The filtered graph, or a tuple containing the filtered graph and a
        list of the dropped edges (if `return_dropped_edges` is True).
    """

    if filter_function == "no_traffic_signals":
        filter_function = _is_edge_traffic_signal
    elif filter_function == "no_unmarked_major":
        filter_function = _is_edge_unmarked_major
    elif filter_function == "no_stopping":
        filter_function = _is_edge_stopping

    dropped_edges = [
        (u, v, k)
        for u, v, k, d in crossing_graph.edges(keys=True, data=True)
        if filter_function(d, crossing_graph.graph["edge_definitions"])
    ]

    filtered_graph = crossing_graph.copy()
    filtered_graph.remove_edges_from(dropped_edges)

    if return_dropped_edges:
        return (filtered_graph, dropped_edges)
    else:
        return filtered_graph


def get_components_after_filter(
    crossing_graph: nx.MultiDiGraph,
    filter_function: Literal["no_traffic_signals", "no_stopping"]
    | Callable[[IntermediateEdgeData, EdgeDefinitions], bool],
    verbose: bool = False,
):
    """
    Filters out edges from a crossing graph, and returns all its connected
    components (after the filtering). Returns them as a new graph, where nodes
    are the connected components (their `geometry` attribute is a
    MultLineString with all the edges in the component), and where edges are
    connections between nodes (that would require going through an edge that
    was dropped during filtering).

    Parameters
    ---
    crossing_graph : MultiDiGraph
        Crossing graph, generated with `generate_crossing_graph`.
    filter_function
        The function to use to filter out edges, only those that return False
        (not filtered) are kept.  Can be one of:

        - `no_traffic_signals`: removes all crossings involving traffic
          signals.
        - `no_unmarked_major`: removes all unmarked crossings along major
          roads (where the pedestrian is not usually allowed the right of
          way).
        - `no_stopping`: removes all crossings where a pedestrian would have
          to stop and wait for incoming cars (traffic signals, as well as
          unmarked crossings along major roads).
        - Any function that takes two arguments: a dictionary with edge data
          (see type `IntermediateEdgeData`), and a dictionary with major and
          minor road types (see type `EdgeDefinitions`); and returns a boolean
          indicating whether the edge is dropped (True) or kept (False).
    verbose : bool, default: False
        Whether to print updates and show progress bars while the components
        are being calculated.

    Returns
    ---
    components_graph : Graph
        A simple, undirected graph containing all components (each as its own
        node), where two components are connected if there is a dropped edge
        that connects nodes from each component. There are no parallel edges,
        meaning two components are connected by only one edge, even if there
        are multiple dropped edges that connected them.
    """

    if verbose:
        print("(1/3) Obtaining filtered graph information...")

    filtered_graph, dropped_edges = filter_edges(
        crossing_graph, filter_function, return_dropped_edges=True
    )

    filtered_graph_undirected = filtered_graph.to_undirected()
    _, edges_gdf = ox.graph_to_gdfs(filtered_graph_undirected)

    if verbose:
        print("(2/3) Obtaining component data...")

    components = []
    node_to_component = {}
    for i, component_nodes in tqdm(
        enumerate(nx.connected_components(filtered_graph_undirected)),
        total=nx.number_connected_components(filtered_graph_undirected),
        disable=not verbose,
    ):
        for u in component_nodes:
            node_to_component[u] = i
        # get edges whose endpoints are in this component
        component_edges = edges_gdf[
            edges_gdf.index.get_level_values("u").isin(component_nodes)
            & edges_gdf.index.get_level_values("v").isin(component_nodes)
        ]
        components.append({"geometry": component_edges.union_all()})

    if verbose:
        print("(3/3) Creating new components graph...")

    # get new edges (dropped edges that connected two different components)
    dropped_edges_df = pd.DataFrame(dropped_edges, columns=("u", "v", "k"))
    new_edges = list(
        dropped_edges_df.assign(
            u=dropped_edges_df["u"].map(node_to_component),
            v=dropped_edges_df["v"].map(node_to_component),
        )[["u", "v"]].itertuples(index=False, name=None)
    )

    # create new graph and add node data
    components_graph = nx.Graph(new_edges)
    components_graph.add_nodes_from(enumerate(components))

    if verbose:
        print(
            "Graph generated, "
            f"{components_graph.number_of_nodes()} nodes, "
            f"{components_graph.number_of_edges()} edges."
        )

    return components_graph
