import pickle
from ast import literal_eval
from pathlib import Path

import osmnx as ox
from networkx import MultiDiGraph


def save_graphml(crossing_graph: MultiDiGraph, filepath: str | Path):
    """
    Saves the crossing graph to a GraphML file.

    This is a copy of OSMnx's `save_graphml` function, it's just for symmetry
    (the loading function is different, and it would be awkward to load the
    file with one library and save it with another).

    Parameters
    ---
    crossing_graph : MultiDiGraph
        The graph to save, generated with `generate_crossing_graph`.
    filepath : str or Path
        The path to save the crossing graph to.
    """

    return ox.io.save_graphml(crossing_graph, filepath)


def load_graphml(filepath: str | Path):
    """
    Loads a crossing graph from a GraphML file, converting attributes to their
    correct types when necessary (GraphML saves them all as strings).

    Parameters
    ---
    filepath : str or Path
        The path to load the crossing graph from.

    Returns
    ---
    crossing_graph : MultiDiGraph
        The loaded crossing graph.
    """

    return ox.io.load_graphml(
        filepath,
        node_dtypes={"index": int, "base_node": int, "angle": float},
        edge_dtypes={"edge_osmid": int, "edge_crossable": ox.io._convert_bool_string},
        graph_dtypes={
            "buffered": ox.io._convert_bool_string,
            "node_definitions": literal_eval,
            "edge_definitions": literal_eval,
        },
    )


def save_pickle(crossing_graph: MultiDiGraph, filepath: str | Path):
    """
    Saves the crossing graph to a pickle file.

    Parameters
    ---
    crossing_graph : MultiDiGraph
        The graph to save, generated with `generate_crossing_graph`.
    filepath : str or Path
        The path to save the crossing graph to.
    """

    with open(filepath, "wb") as f:
        pickle.dump(crossing_graph, f, protocol=pickle.HIGHEST_PROTOCOL)

      
def load_pickle(filepath: str | Path):
    """
    Loads a crossing graph from a pickle file.

    Parameters
    ---
    filepath : str or Path
        The path to load the crossing graph from.

    Returns
    ---
    crossing_graph : MultiDiGraph
        The loaded crossing graph.
    """

    with open(filepath, "rb") as f:
        crossing_graph = pickle.load(f)
    
    return crossing_graph