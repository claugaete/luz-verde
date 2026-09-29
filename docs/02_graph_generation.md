# Graph generation

## Preliminaries

We will assume that we are working with a fully bidirectional multigraph; that
is, every edge $(u, v, k)$ (where $k$ differentiates multiple possible road
segments that share an origin and destination) implies the existence of an
opposing edge $(v, u, k)$. This makes sense for our purposes, because
pedestrian movement is never one-way. In the rest of this document, we will
not write $k$ to reduce cluttering, but do remember that it exists to
differentiate road segments with the same origin and destination.

We will divide road types into the following categories:
- Major roads: motorways; primary, secondary and tertiary roads. These have
  significant vehicular flow, and so should not be crossed unless explicitly
  allowed.
- Minor roads: Residential roads and cycleways. These have a lower vehicular
  flow, and so can be crossed unless it is specifically disallowed.
- Service roads, pedestrian roads, and footways (including sidewalks). These
  have no high-speed vehicular flow; they can also be crossed unless
  specifically disallowed.

## New graph structure

The basic idea behind this package is that, for every node $u$ in the original
graph (we will call these "base nodes" from now on), we will create
**intermediate nodes** $a = (e, f)$ between every adjacent pair of outgoing
edges from the base node $u$ (in this case, $e = (u, v)$ and $f = (u, w)$).
These nodes will represent the space "between" two intersecting roads, going
clockwise from e (the "left-side" edge) to f (the "right-side" edge).

We will only allow travel between these intermediate nodes. This can occur in
one of two ways:
- **Crossing a road $e$ along a base node $u$**, connecting two intermediate
  nodes, $a$ and $b$, on either side of $e$. This can happen when $a$'s right
  side edge is equal to $b$'s left-side edge (a "clockwise crossing"), or when
  $a$'s left-side edge is equal to $b$'s right-side edge (a "counterclockwise
  crossing").
- **Walking along a road $e$**, between two intermediate nodes, $a$ and $c$,
on the same side of $e$. This can happen when $a$'s left-side edge $e=(u, v)$
is the inverted version of $c$'s right-side edge $e'=(v, u)$ (a "right-side
walk"), or when $a$'s right-side edge is the inverted version of $c$'s
left-side edge (a "left-side walk").

The following diagram shows the original street network in black (nodes and
edges), intermediate nodes in orange, and possible crossings in blue.

![Diagram showing an example of the "crossing" graph, alongside the
original](img/diagram.svg)

We will apply this concept for every road in OpenStreetMap; this includes
sidewalks, so technically we will be standing on either side of the sidewalk.
This is not useful information (and it bloats the graph with unnecessary nodes
and edges), but I decided to make this package as general as possible, and so
we will be treating all roads equally in principle.

### Crossing roads

Given a base node $u$, we will consider it **safe** if it's a marked or
controlled crossing, and we will consider it **unsafe** if crossing along the
node is prohibited. Nodes that are neither safe nor unsafe will be considered
**neutral**.

Given a road $e$, we will consider it **crossable** if it is not a major road.

With these two concepts, we can introduce our first two rules:

> If a road $e$ is crossable, you can always cross it along a base node $u$,
> unless $u$ is unsafe

> If a road $e$ is not crossable, you can only cross it along a base node $u$
> if u is safe.

This implies that it is only safe to cross a major road if there is an
explicit marking for pedestrians to cross along, avoiding the problem that
motivated part of this package.

### Walking along roads

For walking along roads, there is only one rule:

> You can always walk along the side of a road, unless there is no sidewalk,
> or the sidewalk is mapped separately (in that case, you should be walking
> along the edges representing the sidewalk, not the main road).

## Edge cases and other fixes

### Walking along the boundary

There are some problems when walking along roads on the boundary of your
original graph. Because there are no roads outside the boundary, the algorithm
will assume that you can safely walk along the entire outer edge of the study
area, which is not necessarily the case and might introduce routing errors
(where the algorithm makes you walk along the boundary to avoid unsafe
crossings, inadvertently making you walk across roads it didn't know existed).
Because of this, we will add an extra rule:

> If e and f are two roads that belong to the boundary of the study area, then
> no intermediate nodes will be created between $e$ and $f$.

### Intersections near traffic signals

There is an extra rule that helps when combining traffic signals and minor
roads. Take this intersection in Providencia, Santiago:

![Intersection of Román Díaz with Francisco Bilbao, according to
OpenStreetMap. Providencia, Santiago](img/roman-diaz.png)

Because Román Díaz is residential (and no separate sidewalk is marked on the
main road), the algorithm assumes that you can walk along Román Díaz to the
intersection with Francisco Bilbao, and cross Román Díaz along the south side
of Francisco Bilbao (orange arrows). In reality, you should be using the
designated crossing place on the sidewalk (blue arrows). To solve this, we add
one more rule:

> If a base node $u$ is near traffic signals, but it is not a safe node, then
> it is marked as unsafe.

We use network distance (not euclidean distance) to calculate whether a
traffic signal is close enough to $u$; by default, it must be no more than 20
meters away, but this can be changed. This network distance only considers
major and minor roads; it doesn't travel through service roads, pedestrian
roads or footways.

### Mislabeled crossings

One common mistake in Santiago's OpenStreetMap data is to label pedestrian
crossings with traffic signals with the `crossing=marked` tag, instead of the
correct `crossing=traffic_signals` tag. Because of this, we will add an
optional rule:

> If a crossing with the `crossing=marked` tag is near traffic signals, it
> will be changed to the `crossing=traffic_signals` tag.

This is obviously not going to be correct in all cases, but it is a temporary
heuristic while a more permanent fix (retagging all incorrect crossings) is
worked on. By default, we retag marked crossing that are less than 30 meters
away from a traffic signal.