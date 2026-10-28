# %% [markdown]
# # Example 2: geometry buffering
#
# In this example, we will modify the geometries for the crossing graph
# generated in the previous example, so that every node and edge has its own
# geometry.
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
# If the cell below doesn't work, generate the graph using the previous example
# script (and with the same value for `USE_GREATER_SANTIAGO`).

# %%
file_path = DATA_PATH / "generated-graphs" / f"{file_name}_crossing_graph"

print("Loading graph...")
if USE_GREATER_SANTIAGO:
    crossing_graph = lv.load_pickle(file_path.with_suffix(".pkl"))
else:
    crossing_graph = lv.load_graphml(file_path.with_suffix(".graphml"))
print("Graph loaded.")

# %% [markdown]
# ## What's the problem?

# %% [markdown]
# Technically, you could use the crossing graph generated in the previous
# example and it would work perfectly fine (because the nodes and edges are
# already generated and connected properly). However, if you want to visualize
# the graph, you will quickly notice some issues:
#
# - All intermediate nodes occupy the same position as their base node (which
#   means multiple intermediate nodes can occupy the same position).
# - Crossings along base nodes are represented as a point, not an edge (because
#   the connected intermediate nodes are in the same position). They also have a
#   fixed length of 0.1.
# - Walks along edges are represented by the edge itself (which means you can't
#   differentiate between a left-side and a right-side walk).

# %%
truncated_graph = ox.truncate.truncate_graph_bbox(
    crossing_graph, bbox=(-70.6158548, -33.4327463, -70.6058502, -33.4199045)
)

# %%
gdf_nodes, gdf_edges = ox.graph_to_gdfs(truncated_graph)

# %%
# all intermediate nodes occupy the same position
example_base_node = gdf_nodes.value_counts("base_node").idxmax()
gdf_nodes[gdf_nodes["base_node"] == example_base_node]

# %%
# (put an empty markdown cell below this one if the basemap doesn't load)
# note that:
# - all crossings are points instead of edges (and have a fixed length)
# - walks along roads are represented by the actual road (they are not to one
# side)
gdf_edges.explore()

# %% [markdown]
# ## Fixing it

# %% [markdown]
# To fix it, we can call the function `buffer_crossing_graph_geometry`, which
# takes a parameter `buffer` and moves each intermediate node `buffer` meters
# away from the base node (except for the case where there is only one
# intermediate node for a base node). Crossings along base nodes are represented
# by straight lines between the new intermediate nodes's geometries (each one
# now has its own length). Walks along edges now connect the two intermediate
# nodes's new geometries while trying to follow the original road's curves (this
# is achieved by buffering each edge `buffer` meters and following the exterior
# of this polygon).

# %%
buffered_graph = lv.buffer_crossing_graph_geometry(
    crossing_graph, buffer=1, verbose=True
)

# %% [markdown]
# We can now see that all problems mentioned before have been fixed.

# %%
new_truncated_graph = ox.truncate.truncate_graph_bbox(
    buffered_graph, bbox=(-70.6158548, -33.4327463, -70.6058502, -33.4199045)
)

# %%
new_gdf_nodes, new_gdf_edges = ox.graph_to_gdfs(new_truncated_graph)

# %%
# all intermediate nodes now occupy different positions
new_gdf_nodes[new_gdf_nodes["base_node"] == example_base_node]

# %%
# (put an empty markdown cell below this one if the basemap doesn't load)
# now all crossings are edges (with different lengths), and different-sided
# walks have different geometries
new_gdf_edges.explore()

# %% [markdown]
# We will save the graph with the new geometries, for use in future examples.

# %%
file_path = DATA_PATH / "generated-graphs" / f"{file_name}_buffered_graph"

print("Saving graph...")
if USE_GREATER_SANTIAGO:
    lv.save_pickle(
        buffered_graph,
        file_path.with_suffix(".pkl")
    )
else:
    lv.save_graphml(
        buffered_graph,
        file_path.with_suffix(".graphml")
    )
print("Graph saved.")
