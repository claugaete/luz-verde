# %% [markdown]
# # Example 4: filtering and components
#
# In this final example, we will filter edges from the graph, and get the weakly
# connected components of the graph after filtering.
#
# In the cell below, you can select whether you want to run the example for all
# of Greater Santiago or just one municipality (Providencia).

# %%
USE_GREATER_SANTIAGO = True

# %% [markdown]
# ## Imports

# %%
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import networkx as nx
from shapely import Point

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

# %%
PROJECTED_CRS = 5361


def prettify_plot(ax: plt.Axes, basemap_zoom: int):
    """Makes the plot prettier (adds a basemap, makes it more square, removes
    ticks.)"""

    ax.set_xticks([])
    ax.set_yticks([])

    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()

    # expand y-axis (to fit attribution)
    y0 = y0 - (y1 - y0) / 10
    y1 = y1 + (y1 - y0) / 10

    # makes it more square
    xlen = x1 - x0
    ylen = y1 - y0
    extra = (xlen - ylen) / 2
    if xlen > ylen:
        ax.set_ylim(y0 - extra, y1 + extra)
    else:
        ax.set_xlim(x0 + extra, x1 - extra)
        ax.set_ylim(y0, y1)

    cx.add_basemap(ax, zoom=basemap_zoom, crs=PROJECTED_CRS)


# %% [markdown]
# If the cell below doesn't work, generate the graph using the [geometry example
# script](02_geometry.ipynb) (and with the same value for
# `USE_GREATER_SANTIAGO`).

# %%
file_path = DATA_PATH / "generated-graphs" / f"{file_name}_buffered_graph"

print("Loading graph...")
if USE_GREATER_SANTIAGO:
    crossing_graph = lv.load_pickle(file_path.with_suffix(".pkl"))
else:
    crossing_graph = lv.load_graphml(file_path.with_suffix(".graphml"))
print("Graph loaded.")

# %% [markdown]
# ## Obtaining the new graphs
#
# Firstly, we can drop the edges that meet a certain condition from the crossing
# graph, using the function `filter_edges`. There are some preset filter
# functions, but you can also create your own.
#
# In this case, we will be dropping all unmarked crossings along major roads,
# because they are not always safe (vehicles might assume they have the right of
# way, and there might be a large volume of vehicles if the road is major).

# %%
crossing_graph = lv.filter_edges(crossing_graph, filter_function="no_unmarked_major")

# %% [markdown]
# You can also get the connected components from a crossing graph after applying
# a filter. In this case, we will get components after dropping all traffic
# signal crossings, meaning each component is a subnetwork where you can get
# from any point to any other point without using any traffic signal crossings,
# and two components have an edge between them if there is a traffic signal
# crossing that connects them.

# %%
components_graph = lv.get_components_after_filter(
    crossing_graph, filter_function="no_traffic_signals", verbose=True
)

# %% [markdown]
# ## Visualizing components

# %% [markdown]
# In order to more easily visualize the components, we will convert the "nodes"
# (each representing a component) to a GeoDataFrame.

# %%
components_gdf = (
    gpd.GeoDataFrame.from_dict(
        dict(components_graph.nodes(data=True)),
        orient="index",
        crs=4326,
    )
    .sort_index()
    .to_crs(PROJECTED_CRS)
)

# %% [markdown]
# For example, we can take the 10 largest components (by measuring the area of
# their convex hulls) and visualize them.

# %%
largest_components = (
    components_gdf.assign(hull_area=components_gdf.convex_hull.area)
    .sort_values("hull_area", ascending=False)
    .iloc[:10]
)

# %%
ax = largest_components.plot(
    categorical=True,
    cmap="tab10",
    linewidth=0.5,
)

prettify_plot(ax, basemap_zoom=12 if USE_GREATER_SANTIAGO else 14)
ax.set_title(
    "Largest components walkable without traffic signals\n"
    f"in {'Santiago' if USE_GREATER_SANTIAGO else 'Providencia'}"
)

ax.get_figure().savefig(
    DATA_PATH / "img" / f"{file_name}_largest_components.png",
    bbox_inches="tight",
    dpi=300,
)

# %% [markdown]
# We can also take a starting point (Plaza de Armas for the whole of Santiago,
# Palacio Falabella for Providencia), and calculate the minimum number of
# traffic signals that need to be crossed in order to get from the starting
# point to every other point in the study area.

# %%
if USE_GREATER_SANTIAGO:
    start_coords = (-70.6505028, -33.4378262)
    place_name = "Plaza de Armas"
else:
    start_coords = (-70.6097930, -33.4322768)
    place_name = "Palacio Falabella"
start_point = gpd.GeoSeries([Point(*start_coords)], crs=4326).to_crs(PROJECTED_CRS)
start_component = components_gdf.sindex.nearest(start_point, return_all=False)[1][0]

# %%
components_gdf["shortest_path"] = components_gdf.index.map(
    nx.shortest_path_length(components_graph, start_component)
)

# %%
ax = components_gdf.plot(
    "shortest_path",
    legend=True,
    legend_kwds={"label": "Number of traffic signals"},
    linewidth=0.5,
)
start_point.plot(ax=ax, color="tab:orange", zorder=10)
handles = [
    mlines.Line2D([], [], color="white", marker="o", markerfacecolor="tab:orange"),
]
labels = [place_name]
ax.legend(handles, labels, loc="upper right")


prettify_plot(ax, basemap_zoom=12 if USE_GREATER_SANTIAGO else 14)
ax.set_title(
    "Minimum number of traffic signals to get\n"
    f"from {place_name} to anywhere in "
    + ("Santiago" if USE_GREATER_SANTIAGO else "Providencia")
)

ax.get_figure().savefig(
    DATA_PATH / "img" / f"{file_name}_minimum_traffic_signals.png",
    bbox_inches="tight",
    dpi=300,
)
