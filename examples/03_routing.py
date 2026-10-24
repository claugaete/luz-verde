# %%
# %reload_ext autoreload
# %autoreload 2

# %% [markdown]
# # Example 3: routing
#
# In this example, we will (finally!) use the crossing graph to get routes
# between places.
#
# In the cell below, you can select whether you want to run the example for all
# of Greater Santiago or just one municipality (Providencia).

# %%
USE_GREATER_SANTIAGO = False

# %% [markdown]
# ## Imports

# %%
from pathlib import Path

import contextily as cx
import matplotlib.lines as mlines
import matplotlib.pyplot as plt

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
def prettify_plot(ax: plt.Axes):
    """Makes the plot prettier (adds a basemap, makes it more square, removes
    ticks.)"""
    
    ax.set_xticks([])
    ax.set_yticks([])

    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    
    # expand y-axis (to fit attribution)
    y0 = y0 - (y1 - y0) / 4
    y1 = y1 + (y1 - y0) / 4

    # makes it more square
    xlen = x1 - x0
    ylen = y1 - y0
    extra = (xlen - ylen) / 2
    if xlen > ylen:
        ax.set_ylim(y0 - extra, y1 + extra)
    else:
        ax.set_xlim(x0 + extra, x1 - extra)
        ax.set_ylim(y0, y1)

    cx.add_basemap(ax, zoom=17, crs=4326)


# %% [markdown]
# If the cell below doesn't work, generate the graph using the previous example
# script (and with the same value for `USE_GREATER_SANTIAGO`).

# %%
print("Loading graph...")
crossing_graph = lv.load_graphml(
    DATA_PATH / "generated-graphs" / f"{file_name}_buffered_graph.graphml",
)
print("Graph loaded.")

# %% [markdown]
# ## Getting routes

# %% [markdown]
# In order to get a route, we first need to assign a travel time (in seconds) to
# each edge. This is done with the `add_edge_travel_times` function, which
# receives the crossing graph, a walking speed (in km/h), and a travel time
# calculating function.

# %%
lv.add_edge_travel_times(crossing_graph, walk_speed=3.6, travel_time_function="base")

# %% [markdown]
# Now we can route between two points using `get_route_gdf`, which takes a
# starting and ending point, and returns a GeoDataFrame with the list of edges
# that minimize travel time between those points.

# %%
start1 = (-70.6075311, -33.4375191)
end1 = (-70.6070015, -33.4413761)

# %%
shortest_route = lv.get_route_gdf(crossing_graph, start=start1, end=end1)

# (add empty markdown cell below if the basemap doesn't load)
shortest_route.explore()

# %% [markdown]
# We can also use different travel time functions to get different results; for
# example, minimizing stops (e.g. traffic signals) gives us a longer route, but
# one which avoids three traffic signals. It avoids one by using a pedestrian
# bridge that goes over the street, and another two by using a zebra crossing
# that is a bit farther away.

# %%
lv.add_edge_travel_times(
    crossing_graph, walk_speed=3.6, travel_time_function="minimize_stops"
)
no_stops_route = lv.get_route_gdf(crossing_graph, start=start1, end=end1)

# %%
fig, ax = plt.subplots()
shortest_route.plot(ax=ax, color="tab:blue", alpha=0.5)
no_stops_route.plot(ax=ax, color="tab:orange", alpha=0.5)
ax.plot(*list(zip(start1, end1)), marker="o", linewidth=0)

handles = [
    mlines.Line2D([], [], color="white", marker="o", markerfacecolor="tab:blue"),
    mlines.Line2D([], [], color="tab:blue", marker=""),
    mlines.Line2D([], [], color="tab:orange", marker=""),
]
labels = [
    "Start/end",
    "Shortest route",
    "Route with fewest stops",
]
ax.legend(handles, labels, loc=(0, -0.2))

prettify_plot(ax)

ax.get_figure().savefig(
    DATA_PATH / "img" / f"{file_name}_fastest_route_comparison.png",
    bbox_inches="tight",
    dpi=300,
)
