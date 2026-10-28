from collections.abc import Callable
from typing import Literal

import geopandas as gpd
import networkx as nx
import osmnx as ox

from .common import EdgeDefinitions, IntermediateEdgeData, match_road


def _get_travel_time_base(
    data: IntermediateEdgeData, walk_speed: float, edge_definitions: EdgeDefinitions
) -> float:
    """
    Gets the travel time for each edge in the crossing graph, simply
    multiplying its length by a certain walking speed.

    Parameters
    ---
    data : IntermediateEdgeData
        Data for the edge.
    walk_speed : float
        Walking speed, in km/h.
    edge_defintions : EdgeDefinitions
        Definitions for major and minor roads (unused in this function).

    Returns
    ---
    travel_time : float
        Travel time for the edge, in seconds.
    """

    return data.get("length") / walk_speed * 3.6


def _get_travel_time_crossing_penalty(
    data: IntermediateEdgeData,
    walk_speed: float,
    edge_definitions: EdgeDefinitions,
):
    """
    Gets the travel time for each edge in the crossing graph, multiplying its
    length by a certain walking speed and adding a certain penalty for
    crossings depending on their type:

    - Traffic signal crossings (along major and minor roads) and unmarked
      crossings along major roads get a 30 second penalty (waiting for the
      light to turn green or for an opening to appear).
    - Other crossings get a 2 second penalty (looking both ways before
      crossing).

    Parameters
    ---
    data : IntermediateEdgeData
        Data for the edge.
    walk_speed : float
        Walking speed, in km/h.
    edge_defintions : EdgeDefinitions
        Definitions for major and minor roads.

    Returns
    ---
    travel_time : float
        Travel time for the edge, in seconds.
    """

    # base travel time (in seconds)
    base_time = _get_travel_time_base(data, walk_speed, edge_definitions)

    if data.get("type") == "cross":
        match data.get("node_crossing"):
            case "traffic_signals":
                base_time += 30
            case "unmarked" if match_road(
                data.get("edge_highway"), edge_definitions["major"]
            ):
                base_time += 30
            case _:
                base_time += 2

    return base_time


def _get_travel_time_minimize_stops(
    data: IntermediateEdgeData,
    walk_speed: float,
    edge_definitions: EdgeDefinitions,
):
    """
    Gets the travel time for each edge in the crossing graph, adding a huge
    penalty for places where a person would have to stop for more than a
    second (traffic signals crossings, and unmarked crossings along major
    roads). This is so that a router that minimizes travel time will also
    minimize the number of times stopped.

    Parameters
    ---
    data : IntermediateEdgeData
        Data for the edge.
    walk_speed : float
        Walking speed, in km/h.
    edge_defintions : EdgeDefinitions
        Definitions for major and minor roads.

    Returns
    ---
    travel_time : float
        Travel time for the edge, in seconds.
    """

    # base travel time (in seconds)
    base_time = _get_travel_time_base(data, walk_speed, edge_definitions)

    if data.get("type") == "cross":
        match data.get("node_crossing"):
            case "traffic_signals":
                base_time += 1_000_000
            case "unmarked" if match_road(
                data.get("edge_highway"), edge_definitions["major"]
            ):
                base_time += 1_000_000
            case _:
                pass

    return base_time


def add_edge_travel_times(
    crossing_graph: nx.MultiDiGraph,
    walk_speed: float = 3.6,
    travel_time_function: Literal["base", "crossing_penalty", "minimize_stops"]
    | Callable[[IntermediateEdgeData, float, EdgeDefinitions | None], float] = "base",
) -> None:
    """
    Adds the `travel_time` attribute to every edge in the crossing graph,
    according to a certain walk speed and travel time function. This modifies
    the graph, giving every edge its estimated travel time in seconds.

    Parameters
    ---
    crossing_graph : MultiDiGraph
        Crossing graph, generated with `generate_crossing_graph`.
    walk_speed : float, default: 3.6
        Walking speed, in km/h.
    travel_time_function : default: "base"
        The function to use to calculate travel times. Can be one of:

        - `base`: simply multiply the length of each edge by the walking
          speed.
        - `crossing_penalty`: like `base`, but adds a penalty to crossings: 30
          seconds for crossings where a person is expected to wait (traffic
          signals, and unmarked crossings along major roads), and 2 seconds
          for other crossings (looking both ways before continuing).
        - `minimize_stops`: like `base`, but adds a huge penalty for crossings
          where a person is expected to wait (traffic signals, and unmarked
          crossings along major roads), in order to minimize the number of
          times the person stops.
        - Any function that takes three arguments: a dictionary with edge data
          (see type `IntermediateEdgeData`), a float representing walking
          speed, and a dictionary with major and minor road types (see type
          `EdgeDefinitions`); and returns an estimated travel time for the
          edge.
    """

    if travel_time_function == "base":
        travel_time_function = _get_travel_time_base
    elif travel_time_function == "crossing_penalty":
        travel_time_function = _get_travel_time_crossing_penalty
    elif travel_time_function == "minimize_stops":
        travel_time_function = _get_travel_time_minimize_stops

    edge_definitions = crossing_graph.graph["edge_definitions"]

    nx.set_edge_attributes(
        crossing_graph,
        {
            (u, v, k): travel_time_function(d, walk_speed, edge_definitions)
            for u, v, k, d in crossing_graph.edges(keys=True, data=True)
        },
        name="travel_time",
    )


def get_route_gdf(
    crossing_graph: nx.MultiDiGraph,
    start: tuple[float, float],
    end: tuple[float, float],
) -> gpd.GeoDataFrame:
    """
    Given a crossing graph, gets the list of edges that form the fastest route
    between the start and end points.

    Parameters
    ---
    crossing_graph : MultiDiGraph
        Crossing graph, generated with `generate_crossing_graph`. Must have
        travel times calculated; this is done with `add_edge_travel_times`.
    start : tuple[float, float]
        X (longitude) and Y (latitude) coordinates for the starting point, in
        WSG 84 (decimal degrees).
    end : tuple[float, float]
        X (longitude) and Y (latitude) coordinates for the ending point, in
        WSG 84 (decimal degrees).

    Returns
    ---
    gdf_edges : GeoDataFrame
        A GeoDataFrame with the list of edges that form the fastest route.
    """

    # get nearest nodes to start and end point
    start_node = ox.nearest_nodes(crossing_graph, *start)
    end_node = ox.nearest_nodes(crossing_graph, *end)

    route = ox.shortest_path(crossing_graph, start_node, end_node, weight="travel_time")

    return ox.routing.route_to_gdf(crossing_graph, route)
