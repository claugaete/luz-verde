# `luz-verde`

`luz-verde` ("green light" in Spanish) is a Python package that determines
safe places to cross streets when walking between two points, using
OpenStreetMap data. It does this by taking a street network graph (generated
with [OSMnx](https://osmnx.readthedocs.io/en/stable/)), and using it to create
a new graph, where nodes represent the places you can safely stand in (not the
places where the roads intersect), and edges are only allowed where crossing a
street is deemed safe.

This package uses [`uv`](https://docs.astral.sh/uv/). To install all necessary
dependencies, run `uv sync` from the main directory.

The [`docs`](docs) directory contains extra information on how the package
works and the motivations behind it. The [`examples`](examples) directory
contains Python scripts (that can be converted into Jupyter notebooks) with
examples that showcase possible use cases for the package.