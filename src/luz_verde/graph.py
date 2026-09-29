import re
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

DEFAULT_ROAD_DEFINITIONS = {
    "major": ["motor", "primary", "secondary", "tertiary"],
    "minor": ["residential", "cycleway"],
}

DEFAULT_CROSSING_DEFINITIONS = {
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


def _coalesce_crossing_types(gdf_nodes: gpd.GeoDataFrame) -> pd.Series:
    """
    Coalesces all possible crossing types for each row in `gdf_nodes` into
    five categories:

    - `traffic_signals`: crossing that contain traffic signals.
    - `uncontrolled`: marked crossing with no traffic signals (e.g. zebra
      crossings).
    - `unmarked`: other crossings that are not explicitly painted into the
      street.
    - `no`: where crossing is not allowed.
    - `none`: where crossing is neither explicitly allowed not explicitly
      disallowed.

    Returns a new column with the coalesced crossings.
    """

    conditions = {
        "traffic_signals": (
            gdf_nodes["crossing"].str.contains("traffic_signals")
            | gdf_nodes["highway"].str.contains("traffic_signals")
            | (gdf_nodes["crossing:signals"] == "yes")
        ),
        "uncontrolled": (
            gdf_nodes["crossing"].isin(["uncontrolled", "marked", "zebra"])
        ),
        "unmarked": (gdf_nodes["crossing"].str.contains("unmarked")),
        "no": (gdf_nodes["crossing"].isin["no", "separate"]),
    }

    return np.select(conditions.keys(), conditions.values(), "none")


def _set_node_safety(
    gdf_nodes: gpd.GeoDataFrame, node_definitions: dict[str, list[str]]
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
    Sets whether each edge in `gdf_nodes` is crossable based on
    `edge_defintions`. If the value in `gdf_edges["highway"]` matches with one
    of the regex patterns in `uncrossable_pats`, then the edge is declared
    uncrossable. If none match, then the edge is declared crossable by
    default.

    Returns a new column with the "crossability" of each edge.
    """

    return np.where(
        gdf_edges["highway"].str.contains("|".join(uncrossable_pats)),
        "uncrossable",
        "crossable",
    )
