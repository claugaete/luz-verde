# %% [markdown]
# # Example 3: routing
#
# In this example, we will (finally!) use the crossing graph to get routes
# between places.
#
# In the cell below, you can select whether you want to run the example for all
# of Greater Santiago or just one municipality (Providencia). Do consider that
# this example gives the exact same results in either case (we are always
# generating routes inside Providencia).

# %%
USE_GREATER_SANTIAGO = False

# %% [markdown]
# ## Imports

# %%
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import networkx as nx
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
# If the cell below doesn't work, generate the graph using the previous example
# script (and with the same value for `USE_GREATER_SANTIAGO`).

# %%
file_path = DATA_PATH / "generated-graphs" / f"{file_name}_buffered_graph"

print("Loading graph...")
if USE_GREATER_SANTIAGO:
    crossing_graph = lv.load_pickle(file_path.with_suffix(".pkl"))
else:
    crossing_graph = lv.load_graphml(file_path.with_suffix(".graphml"))
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
# bridge that goes over the street, and another two by using zebra crossings
# that are a bit farther away.

# %%
lv.add_edge_travel_times(
    crossing_graph, walk_speed=3.6, travel_time_function="minimize_stops"
)
no_stops_route = lv.get_route_gdf(crossing_graph, start=start1, end=end1)

# %%
# get the number of stops in the shortest route by getting the updated weights
# for each edge, and dividing by 1 million (the travel time penalty for each
# stop)
stops_for_shortest_route = (
    sum(crossing_graph[u][v][k]["travel_time"] for u, v, k in shortest_route.index)
    // 1_000_000
)

# %%
fig, ax = plt.subplots()
shortest_route.to_crs(PROJECTED_CRS).plot(
    ax=ax, color="tab:blue", alpha=0.5, linewidth=2
)
no_stops_route.to_crs(PROJECTED_CRS).plot(
    ax=ax, color="tab:orange", alpha=0.5, linewidth=2
)
gpd.GeoSeries([shapely.Point(start1), shapely.Point(end1)], crs=4326).to_crs(
    PROJECTED_CRS
).plot(ax=ax, zorder=10)

handles = [
    mlines.Line2D([], [], color="white", marker="o", markerfacecolor="tab:blue"),
    mlines.Line2D([], [], color="tab:blue", marker=""),
    mlines.Line2D([], [], color="tab:orange", marker=""),
]
labels = [
    "Start/end",
    (
        f"Shortest route ({shortest_route['length'].sum():.0f}m, "
        f"{stops_for_shortest_route:.0f} stops)"
    ),
    (
        f"Route with fewest stops ({no_stops_route['length'].sum():.0f}m, "
        f"{no_stops_route['travel_time'].sum() // 1_000_000:.0f} stops)"
    ),
]
ax.legend(handles, labels, loc=(0, -0.2))

prettify_plot(ax, basemap_zoom=17)

fig.suptitle("Comparison between optimal routes with\ndifferent weights", y=1.0)
ax.set_title(
    'A "stop" is a traffic signal or an unmarked crossing along a major road',
    fontsize=8,
)

ax.get_figure().savefig(
    DATA_PATH / "img" / "fastest_route_comparison.png",
    bbox_inches="tight",
    dpi=300,
)

# %% [markdown]
# ## Comparing generated routes with OSMnx's default

# %% [markdown]
# We can load the pedestrian street network OSMnx generates for Providencia, and
# compare the routes it generates with the ones given by the crossing graph.

# %%
with open(DATA_PATH / "providencia.geojson", "r") as f:
    providencia_polygon = shapely.from_geojson(f.read())

# %%
ox.settings.cache_folder = DATA_PATH / "osmnx-cache"
osmnx_graph = ox.graph_from_polygon(providencia_polygon, network_type="walk")

# manually assign the travel times to the osmnx graph
WALK_SPEED = 3.6
nx.set_edge_attributes(
    osmnx_graph,
    {
        (u, v, k): d.get("length") / WALK_SPEED * 3.6
        for u, v, k, d in osmnx_graph.edges(keys=True, data=True)
    },
    name="travel_time",
)

# %%
# assign travel times to crossing graph
lv.add_edge_travel_times(crossing_graph, walk_speed=3.6, travel_time_function="base")

# %%
start2 = (-70.6141384, -33.4304112)
end2 = (-70.6073730, -33.4288358)

# %%
osmnx_route = lv.get_route_gdf(osmnx_graph, start2, end2)
safest_route = lv.get_route_gdf(crossing_graph, start2, end2)

# %% [markdown]
# We can see that `luz-verde` gives a longer route, but it bypasses a crossing
# in which the pedestrian has no right of way. This crossing would require the
# pedestrian to avoid incoming cars in a major street (Av. Pedro de Valdivia),
# so `luz-verde` doesn't include it in its list of valid edges.

# %%
fig, ax = plt.subplots()
osmnx_route.to_crs(PROJECTED_CRS).plot(ax=ax, color="tab:blue", alpha=0.5, linewidth=2)
safest_route.to_crs(PROJECTED_CRS).plot(
    ax=ax, color="tab:orange", alpha=0.5, linewidth=2
)
gpd.GeoSeries([shapely.Point(start2), shapely.Point(end2)], crs=4326).to_crs(
    PROJECTED_CRS
).plot(ax=ax, zorder=10)

handles = [
    mlines.Line2D([], [], color="white", marker="o", markerfacecolor="tab:blue"),
    mlines.Line2D([], [], color="tab:blue", marker=""),
    mlines.Line2D([], [], color="tab:orange", marker=""),
]
labels = [
    "Start/end",
    f"OSMnx ({osmnx_route['length'].sum():.0f}m, unsafe crossings)",
    f"luz-verde ({safest_route['length'].sum():.0f}m, safe crossings)",
]
ax.legend(handles, labels, loc=(0, -0.2))

prettify_plot(ax, basemap_zoom=17)

ax.set_title("Comparison between OSMnx and luz-verde routes")

ax.get_figure().savefig(
    DATA_PATH / "img" / "osmnx_comparison.png",
    bbox_inches="tight",
    dpi=300,
)
