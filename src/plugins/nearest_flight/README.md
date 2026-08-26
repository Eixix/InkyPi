# Nearest Flight

Displays the closest currently airborne aircraft to a configured latitude and longitude. Data comes from the public [OpenSky Network REST API](https://openskynetwork.github.io/opensky-api/rest.html).

Configure a location name, coordinates, and a search radius between 1 and 250 km. The display includes the departure and destination airports, airline/operator, registration, manufacturer and aircraft model, as well as distance, bearing, altitude, ground speed, heading, and vertical trend.

Set **Display faces** to the compass direction the physical screen is facing. The proximity arrow is rotated relative to that direction, so an up arrow means the aircraft is straight ahead. Four high-contrast accent themes are available.

No API key is required. Live positions come from OpenSky and aircraft/route enrichment comes from [ADSBDB](https://www.adsbdb.com/). Anonymous request limits apply. Some private, charter, military, or otherwise unmatched flights will not have route or aircraft metadata; the live telemetry remains visible in that case.
