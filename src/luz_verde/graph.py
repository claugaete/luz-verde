from collections.abc import Callable
from pathlib import Path

import networkx as nx
import osmnx as ox


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
