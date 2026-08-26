# Nearest Flight

Displays the closest currently airborne aircraft to a configured latitude and longitude. Data comes from the public [OpenSky Network REST API](https://openskynetwork.github.io/opensky-api/rest.html).

Configure a location name, coordinates, and a search radius between 1 and 250 km. The display includes the callsign (when broadcast), ICAO24 identifier, origin country, distance and bearing from the configured point, altitude, ground speed, heading, and vertical trend.

No API key is required. OpenSky's anonymous request quota applies, so the plugin should not be refreshed more often than necessary. OpenSky provides live ADS-B state vectors, not commercial schedules or guaranteed route/destination information.
