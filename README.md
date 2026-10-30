# `luz-verde`

`luz-verde` ("green light" in Spanish) is a Python package that determines
safe places to cross streets when walking between two points, using
OpenStreetMap data. It does this by taking a street network graph (generated
with [OSMnx](https://osmnx.readthedocs.io/en/stable/)), and using it to create
a new graph, where nodes represent the places you can safely stand in (not the
places where the roads intersect), and edges are only allowed where crossing a
street is deemed safe.

By assigning different travel times to different types of edges (e.g.
crossings that meet a certain condition), you can get the optimal routes
according to criteria other than route length.

<p>
    <img
        align="middle"
        alt="Comparison between OSMnx and luz-verde routes"
        src="docs/img/osmnx_comparison.png"
        width="49%"
    />
    <img
        align="middle"
        alt="Comparison between optimal routes with different weights"
        src="docs/img/fastest_route_comparison.png"
        width="49%"
    />
</p>

You can also filter out certain edges, and get the connected components for
the filtered graph (i.e. areas where you can go from any node to any other
node without using the dropped edges).

<p>
    <img
        align="middle"
        alt="Largest components walkable without traffic signals in Santiago"
        src="docs/img/gran_santiago_largest_components.png"
        width="49%"
    />
    <img
        align="middle"
        alt="Minimum number of traffic signals to get from Plaza de Armas to anywhere in Santiago"
        src="docs/img/gran_santiago_minimum_traffic_signals.png"
        width="49%"
    />
</p>

## Installation and usage

This package uses [`uv`](https://docs.astral.sh/uv/). To install all necessary
dependencies, run `uv sync` from the main directory.

The [`docs`](docs) directory contains extra information on how the package
works and the motivations behind it. The [`examples`](examples) directory
contains Python scripts (that can be converted into Jupyter notebooks) with
examples that showcase possible use cases for the package.