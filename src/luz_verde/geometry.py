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
    Given a line string or a linear ring, a start point and an end point,
    takes the substring of the line that goes from the start point to the end
    point.

    If it's a line string, assumes that the start and end point are on the
    line itself.

    If it's a ring, always takes the route increasing from the start point to
    the end point, wrapping around the end/start of the ring if necessary.
    Also projects the points onto the ring if necessary, connecting the actual
    start/end with the projected start/end using straight lines.
    """

    line: LineString | LinearRing = row["geometry"]

    start_point = row["start_geom"]
    end_point = row["end_geom"]
    start_proj = line.project(start_point)
    end_proj = line.project(end_point)

    # if the projection degenerates into a single point, just connect the
    # start and end points with a straight line
    if start_proj == end_proj:
        return LineString([start_point, end_point])

    # if it's a line string, then just return the substring
    if line.geom_type == "LineString":
        return substring(line, start_proj, end_proj)

    # now we assume we are working with a ring
    # "expected" case
    if start_proj < end_proj:
        segment_coords = list(substring(line, start_proj, end_proj).coords)

    # wraparound case
    else:
        seg1 = substring(line, start_proj, line.length)
        seg2 = substring(line, 0, end_proj)
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
      using the angle calculated during graph generation. If the angle is NaN
      (because the intersection contains only pedestrian roads and consists of
      a single intermediate node), then the intermediate node is kept in the
      same position as the original base node.
    - For crossings, connects the start and end nodes with a straight line.
    - For walks along pedestrian roads, connects the start and end points
      along the road itself.
    - For walks along non-pedestrian roads, buffers the edge `buffer` meters,
      and uses the buffered edge as a reference to generate the new geometry
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
        raise ValueError("This graph's geometries have already been buffered.")

    intermediate_nodes, intermediate_edges = ox.graph_to_gdfs(crossing_graph)

    # project the geodataframes (so we can work in meters)
    proj_crs = intermediate_nodes.estimate_utm_crs()
    intermediate_nodes = intermediate_nodes.to_crs(proj_crs)
    intermediate_edges = intermediate_edges.to_crs(proj_crs)

    if verbose:
        print("(1/2) Generating node geometries...")

    # move the nodes `buffer` meters away from the center, according to the
    # angle calculated during graph computation
    dx = (np.cos(intermediate_nodes.angle) * buffer).fillna(0)
    dy = (np.sin(intermediate_nodes.angle) * buffer).fillna(0)
    nodes_buffered = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(
            x=intermediate_nodes.geometry.x + dx,
            y=intermediate_nodes.geometry.y + dy,
            crs=proj_crs,
        ),
        index=intermediate_nodes.index,
    )

    if verbose:
        print("(2/2) Generating edge geometries...")

    # buffer edges, assign the start and end points for each edge
    edges_buffered = intermediate_edges[["type", "side", "geometry"]].copy()
    edges_buffered["start_geom"] = edges_buffered.merge(
        nodes_buffered.geometry, left_on="u", right_index=True
    )["geometry_y"]
    edges_buffered["end_geom"] = edges_buffered.merge(
        nodes_buffered.geometry, left_on="v", right_index=True
    )["geometry_y"]

    crosses_mask = edges_buffered["type"] == "cross"
    walks_mask = edges_buffered["type"] == "walk"
    non_pedestrian_walks_mask = walks_mask & (edges_buffered["side"] != "center")

    # for crosses, connect with a straight line
    edges_buffered.loc[crosses_mask, "geometry"] = edges_buffered.loc[
        crosses_mask
    ].apply(lambda row: LineString([row["start_geom"], row["end_geom"]]), axis=1)

    edges_buffered.loc[non_pedestrian_walks_mask, "geometry"] = (
        edges_buffered.loc[non_pedestrian_walks_mask, "geometry"]
        .buffer(buffer)
        .exterior
    )
    needs_flip = non_pedestrian_walks_mask & (
        edges_buffered["side"].isin(["clockwise", "left"]) & edges_buffered.is_ccw
    ) | (
        edges_buffered["side"].isin(["counterclockwise", "right"])
        & ~edges_buffered.is_ccw
    )
    edges_buffered.loc[needs_flip, "geometry"] = edges_buffered.loc[
        needs_flip, "geometry"
    ].map(lambda ring: ring.reverse())

    # segment the edges for walks between the start and end node
    if verbose:
        tqdm.pandas()
        edges_buffered.loc[walks_mask, "geometry"] = edges_buffered.loc[
            walks_mask
        ].progress_apply(_segment_line, axis=1)
    else:
        edges_buffered.loc[walks_mask, "geometry"] = edges_buffered.loc[
            walks_mask
        ].apply(_segment_line, axis=1)

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
