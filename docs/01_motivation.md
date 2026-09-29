# Motivation

I run occasionally, and I dislike having to cross traffic signals, because
they impede me from keeping a consistent pace across my run. The original
question I wanted to answer was:

> How far can I get without having to cross a traffic signal?

Or, more generally,

> Given two points A and B, what is the route between them that minimizes the
> number of traffic signs you have to cross?

My original idea was to use OpenStreetMap data to fetch all crossings, filter
out traffic signals, and create a graph containing all other types of
crossings. However, I soon realized that OpenStreetMap is not great at
proposing safe (or even realistic) crossing spots. Take this intersection in
Independencia, Santiago, for example:

![Intersection of Fermín Vivaceta with Retiro and Los Ángeles according to
OpenStreetMap. Independencia, Santiago](img/vivaceta-osm.png)

OpenStreetMap (and even Google Maps) recommend you cross Fermín Vivaceta if
you want to get from Retiro to Los Ángeles. However, Fermín Vivaceta is a
two-way street with no crossings of any kind, meaning pedestrians don't have
the right of way and would have to wait for both ways to be cleared of
oncoming cars before crossing. Not only is this slow, but also unsafe: a
distracted driver or pedestrian could easily cause an accident.

![Route from Retiro to Los Ángeles according to Google Maps. Independencia,
Santiago](img/vivaceta-googlemaps.png)

![Intersection of Fermín Vivaceta with Retiro and Los Ángeles from Google
Street View. Independencia, Santiago](img/vivaceta-irl.png)

With this in mind, the new question I wanted to solve was:

> How can you model a street network, so that given two points A and B, you
> can get a route to get from A to B as a pedestrian (only considering "safe"
> crossing places)?

Once this question could be solved, the original problem would be as simple as
removing all crossings involving traffic signals (to see how far one can go
without crossing a traffic signal) or give those edges a very high cost (to
minimize the number of traffic signal crossings in any route).