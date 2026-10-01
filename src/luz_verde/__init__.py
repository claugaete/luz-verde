"""
luz-verde
========

`luz-verde` ("green light" in Spanish) is a Python package that determines
safe places to cross streets when walking between two points, using
OpenStreetMap data.
"""

from .graph import generate_crossing_graph, get_base_graph

__all__ = [
    "get_base_graph",
]
