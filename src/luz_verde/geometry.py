"""
Functions used to modify the geometry of a generated crossing graph.
"""

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely import LinearRing, LineString, remove_repeated_points
from shapely.ops import substring
from tqdm.auto import tqdm


def _segment_line(row: pd.Series):
    """
    Given a linear ring, a start point and an end point, takes the substring
    of the line that goes from the start point to the end point (projecting
    the points onto the line if necessary). Always takes the route increasing
    from the start point to the end point, wrapping around the end/start of
    the ring if necessary.
    """

    ring: LinearRing = row["geometry"]

    start_point = row["start_geom"]
    end_point = row["end_geom"]
    start_proj = ring.project(start_point)
    end_proj = ring.project(end_point)

    # if the projection degenerates into a single point, just connect the
    # start and end points with a straight line
    if start_proj == end_proj:
        return LineString([start_point, end_point])

    # "expected" case
    if start_proj < end_proj:
        segment_coords = list(substring(ring, start_proj, end_proj).coords)

    # wraparound case
    else:
        seg1 = substring(ring, start_proj, ring.length)
        seg2 = substring(ring, 0, end_proj)
        segment_coords = list(seg1.coords) + list(seg2.coords)

    return remove_repeated_points(
        LineString([start_point] + segment_coords + [end_point])
    )


def buffer_crossing_graph_geometry(
    crossing_graph: nx.MultiDiGraph,
    buffer: float = 1,
    verbose: bool = False,
) -> nx.MultiDiGraph:
    """
    Buffers the geometries of the crossing graph. Does the following:

    - Moves each intermediate node `buffer` meters away from its base node,
      using the angle calculated during graph generation.
    - Buffers every edge `buffer` meters, and uses the buffered edge as a
      reference to generate the new geometries for the intermediate edges
      (connecting the new geometries of the intermediate nodes, while moving
      along the buffered edge).

    Parameters
    ---
    crossing_graph : MultiDiGraph
        The original crossing graph, obtained with `generate_crossing_graph`.
    buffer : float, default: 1
        The buffer distance in meters. Values that are too large might
        generate weird geometries for some edges.
    verbose : bool, default: False
        Whether to print updates and show progress bars while the geometries
        are being generated.

    Returns
    ---
    buffered_graph : MultiDiGraph
        A new crossing graph, with the buffered geometries.
    """

    # check if the graph has already been buffered
    if crossing_graph.graph.get("buffered"):
        raise ValueError(
            "This graph's geometries have already been buffered. If you "
            "really wish to buffer them again, pass "
            "`allow_double_buffer=True`."
        )

    intermediate_nodes, intermediate_edges = ox.graph_to_gdfs(crossing_graph)

    # project the geodataframes (so we can work in meters)
    proj_crs = intermediate_nodes.estimate_utm_crs()
    intermediate_nodes = intermediate_nodes.to_crs(proj_crs)
    intermediate_edges = intermediate_edges.to_crs(proj_crs)

    if verbose:
        print("(1/2) Generating node geometries...")

    # move the nodes `buffer` meters away from the center, according to the
    # angle calculated during graph computation
    nodes_buffered = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(
            x=intermediate_nodes.geometry.x + buffer * np.cos(intermediate_nodes.angle),
            y=intermediate_nodes.geometry.y + buffer * np.sin(intermediate_nodes.angle),
            crs=proj_crs,
        ),
        index=intermediate_nodes.index,
    )

    if verbose:
        print("(2/2) Generating edge geometries...")

    # buffer edges, assign the start and end points for each edge
    edges_buffered = gpd.GeoDataFrame(
        data=intermediate_edges["side"],
        geometry=intermediate_edges.buffer(buffer).exterior,
    )
    edges_buffered["start_geom"] = edges_buffered.merge(
        nodes_buffered.geometry, left_on="u", right_index=True
    )["geometry_y"]
    edges_buffered["end_geom"] = edges_buffered.merge(
        nodes_buffered.geometry, left_on="v", right_index=True
    )["geometry_y"]

    # flip the ring if necessary, so that it increases in the same direction
    # of the walk/cross
    needs_flip = (
        edges_buffered["side"].isin(["clockwise", "left"]) & edges_buffered.is_ccw
    ) | (
        edges_buffered["side"].isin(["counterclockwise", "right"])
        & ~edges_buffered.is_ccw
    )
    edges_buffered.loc[needs_flip, "geometry"] = edges_buffered.loc[
        needs_flip, "geometry"
    ].map(lambda ring: ring.reverse())

    # take the substring of the buffered edge that goes from the start to the
    # end point
    if verbose:
        tqdm.pandas()
        edges_buffered.geometry = edges_buffered.progress_apply(_segment_line, axis=1)
    else:
        edges_buffered.geometry = edges_buffered.apply(_segment_line, axis=1)

    # calculate length for edges (projected) and coordinates for nodes
    # (unprojected)
    nodes_buffered = nodes_buffered.to_crs(4326)
    nodes_buffered["x"] = nodes_buffered.geometry.x
    nodes_buffered["y"] = nodes_buffered.geometry.y
    edges_buffered["length"] = edges_buffered.geometry.length
    edges_buffered = edges_buffered.to_crs(4326)

    # assign new geometries to graph
    buffered_graph = crossing_graph.copy()
    buffered_graph.graph["buffered"] = True
    nx.set_node_attributes(
        buffered_graph, nodes_buffered[["x", "y"]].to_dict(orient="index")
    )
    nx.set_edge_attributes(
        buffered_graph, edges_buffered[["geometry", "length"]].to_dict(orient="index")
    )

    if verbose:
        print("New geometries assigned.")

    return buffered_graph
