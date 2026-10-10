# %% [markdown]
# # Example 1: crossing graph generation
#
# In this example, we will download the base street graph for our area of study,
# and convert it into a crossing graph that can be used to route pedestrian
# movement in a more realistic manner.
#
# In the cell below, you can select whether you want to run the example for all
# of Greater Santiago or just one municipality (Providencia).

# %%
USE_GREATER_SANTIAGO = False

# %% [markdown]
# ## Imports

# %%
from pathlib import Path

import osmnx as ox
import shapely

import luz_verde as lv

# %%
try:
    DATA_PATH = Path(__file__).resolve().parent / "data"
    INTERACTIVE = False
except NameError:
    DATA_PATH = Path("data")
    INTERACTIVE = True

if USE_GREATER_SANTIAGO:
    file_name = "gran_santiago"
else:
    file_name = "providencia"

# %% [markdown]
# ## Obtaining the base graph
#
# We first need to download the base street graph for our area of study. In this
# case, we will use a Shapely polygon obtained from a GeoJSON file, but you can
# use [any OSMnx loader
# function](https://osmnx.readthedocs.io/en/stable/user-reference.html#module-osmnx.graph)
# by passing it as `loader_fn`, and providing all its positional parameters as
# `loader_args`.

# %%
with open(DATA_PATH / f"{file_name}.geojson", "r") as f:
    study_area_polygon = shapely.from_geojson(f.read())

# %%
base_graph = lv.get_base_graph(
    study_area_polygon, cache_dir=DATA_PATH / "osmnx-cache", verbose=True
)

# %%
crossing_graph = ox.graph_from_polygon(study_area_polygon, network_type="walk")

# %% [markdown]
# ## Generating the crossing graph
#
# We now generate the crossing graph from the base graph. This is as simple as
# running the `generate_crossing_graph` function. You can customize the
# generated graph by modifying the function's parameters.

# %%
crossing_graph = lv.generate_crossing_graph(
    base_graph, fix_marked_crossings_near_signals=30, verbose=True
)

# %% [markdown]
# That's it! This new graph is valid as an OSMnx street network, so we can
# already use it for things like routing. However, `luz-verde` has other
# features to improve routing and visualizations based on this crossing graph.

# %%
# route from Costanera Center (shopping mall) to Parque Inés de Suárez (park)
start = ox.nearest_nodes(crossing_graph, -70.6054041, -33.4176924)
end = ox.nearest_nodes(crossing_graph,  -70.6118034, -33.4389438)
route = ox.shortest_path(crossing_graph, start, end, weight="length")

# %%
# visualize the route using an interactive map
ox.routing.route_to_gdf(crossing_graph, route).explore()

# %% [markdown]
# ## Saving the crossing graph
#
# We will save the crossing graph to a GraphML file to use it in the other
# examples.
#
# You can also pickle it like any other Python object, which saves space
# (compared to GraphML) and is faster to load, but is less safe if you wish to
# load graphs from unknown sources.

# %%
print("Saving graph...")
ox.io.save_graphml(
    crossing_graph,
    DATA_PATH / "generated-graphs" / f"{file_name}_crossing_graph.graphml"
)
print("Graph saved.")
