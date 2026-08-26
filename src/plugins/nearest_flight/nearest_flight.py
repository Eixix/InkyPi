import logging
import math
from datetime import datetime

import requests

from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session


logger = logging.getLogger(__name__)

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
ADSBDB_AIRCRAFT_URL = "https://api.adsbdb.com/v0/aircraft/{icao24}"
EARTH_RADIUS_KM = 6371.0088


class NearestFlight(BasePlugin):
    """Display the closest airborne aircraft reported by OpenSky."""

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["style_settings"] = True
        return template_params

    def generate_image(self, settings, device_config):
        latitude = self._coordinate(settings, "latitude", -90, 90)
        longitude = self._coordinate(settings, "longitude", -180, 180)
        radius_km = self._radius(settings.get("radius_km", 100))

        aircraft = self._fetch_nearest(latitude, longitude, radius_km)
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        params = {
            "title": settings.get("title") or "Nearest flight",
            "location_name": settings.get("location_name") or "Configured location",
            "aircraft": aircraft,
            "radius_km": round(radius_km),
            "refreshed_at": datetime.now().strftime("%H:%M"),
            "plugin_settings": settings,
        }
        return self.render_image(dimensions, "nearest_flight.html", "nearest_flight.css", params)

    @staticmethod
    def _coordinate(settings, key, minimum, maximum):
        value = settings.get(key)
        if value in (None, ""):
            raise RuntimeError(f"{key.replace('_', ' ').title()} is required.")
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{key.replace('_', ' ').title()} must be a number.") from exc
        if not minimum <= value <= maximum:
            raise RuntimeError(
                f"{key.replace('_', ' ').title()} must be between {minimum} and {maximum}."
            )
        return value

    @staticmethod
    def _radius(value):
        try:
            radius = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Search radius must be a number.") from exc
        if not 1 <= radius <= 250:
            raise RuntimeError("Search radius must be between 1 and 250 km.")
        return radius

    def _fetch_nearest(self, latitude, longitude, radius_km):
        params = self._bounding_box(latitude, longitude, radius_km)
        try:
            response = get_http_session().get(OPENSKY_STATES_URL, params=params, timeout=20)
            if response.status_code == 429:
                raise RuntimeError("OpenSky rate limit reached. Try refreshing later.")
            response.raise_for_status()
            states = response.json().get("states") or []
        except RuntimeError:
            raise
        except (requests.RequestException, ValueError, AttributeError) as exc:
            logger.exception("Unable to retrieve OpenSky aircraft states")
            raise RuntimeError("Could not retrieve live aircraft data from OpenSky.") from exc

        nearest = None
        for state in states:
            parsed = self._parse_state(state, latitude, longitude)
            if parsed and parsed["distance_km"] <= radius_km:
                if nearest is None or parsed["distance_km"] < nearest["distance_km"]:
                    nearest = parsed
        if nearest:
            self._enrich_aircraft(nearest)
        return nearest

    def _enrich_aircraft(self, aircraft):
        """Add aircraft and route metadata without making it required for display."""
        url = ADSBDB_AIRCRAFT_URL.format(icao24=aircraft["icao24"])
        params = {}
        if aircraft["callsign"] != "Unknown flight":
            params["callsign"] = aircraft["callsign"]

        try:
            response = get_http_session().get(url, params=params, timeout=10)
            if response.status_code == 404:
                return
            response.raise_for_status()
            payload = response.json().get("response", {})
            if not isinstance(payload, dict):
                return
            details = payload.get("aircraft") or {}
            route = payload.get("flightroute") or {}
            airline = route.get("airline") or {}

            aircraft.update({
                "registration": details.get("registration") or None,
                "manufacturer": details.get("manufacturer") or None,
                "model": details.get("type") or details.get("icao_type") or None,
                "type_code": details.get("icao_type") or None,
                "operator": airline.get("name") or details.get("registered_owner") or None,
                "origin": self._airport(route.get("origin")),
                "destination": self._airport(route.get("destination")),
                "midpoint": self._airport(route.get("midpoint")),
            })
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            logger.warning("Could not enrich aircraft %s with ADSBDB", aircraft["icao24"], exc_info=True)

    @staticmethod
    def _airport(data):
        if not isinstance(data, dict):
            return None
        code = data.get("iata_code") or data.get("icao_code")
        name = data.get("municipality") or data.get("name")
        if not code and not name:
            return None
        return {"code": code or "—", "name": name or code}

    @staticmethod
    def _bounding_box(latitude, longitude, radius_km):
        lat_delta = radius_km / 111.32
        cos_lat = max(abs(math.cos(math.radians(latitude))), 0.01)
        lon_delta = radius_km / (111.32 * cos_lat)
        return {
            "lamin": max(-90, latitude - lat_delta),
            "lamax": min(90, latitude + lat_delta),
            "lomin": max(-180, longitude - lon_delta),
            "lomax": min(180, longitude + lon_delta),
        }

    @classmethod
    def _parse_state(cls, state, latitude, longitude):
        # OpenSky state vectors are positional arrays documented at /api/states/all.
        if len(state) < 14 or state[5] is None or state[6] is None or state[8] is True:
            return None

        aircraft_lon, aircraft_lat = float(state[5]), float(state[6])
        altitude_m = state[13] if state[13] is not None else state[7]
        distance = cls._distance_km(latitude, longitude, aircraft_lat, aircraft_lon)
        bearing = cls._bearing(latitude, longitude, aircraft_lat, aircraft_lon)
        heading = state[10]
        vertical_rate = state[11]
        return {
            "icao24": (state[0] or "").upper(),
            "callsign": (state[1] or "").strip() or "Unknown flight",
            "country": state[2] or "Unknown origin",
            "distance_km": round(distance, 1),
            "bearing": cls._compass_direction(bearing),
            "altitude_m": round(float(altitude_m)) if altitude_m is not None else None,
            "speed_kmh": round(float(state[9]) * 3.6) if state[9] is not None else None,
            "heading": round(float(heading)) if heading is not None else None,
            "heading_arrow": cls._heading_arrow(heading),
            "vertical_trend": cls._vertical_trend(vertical_rate),
            "registration": None,
            "manufacturer": None,
            "model": None,
            "type_code": None,
            "operator": None,
            "origin": None,
            "destination": None,
            "midpoint": None,
        }

    @staticmethod
    def _distance_km(lat1, lon1, lat2, lon2):
        lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _bearing(lat1, lon1, lat2, lon2):
        lat1, lat2 = math.radians(lat1), math.radians(lat2)
        dlon = math.radians(lon2 - lon1)
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    @staticmethod
    def _compass_direction(degrees):
        directions = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        return directions[int((degrees + 22.5) // 45) % 8]

    @staticmethod
    def _heading_arrow(heading):
        if heading is None:
            return "·"
        return ("↑", "↗", "→", "↘", "↓", "↙", "←", "↖")[int((float(heading) + 22.5) // 45) % 8]

    @staticmethod
    def _vertical_trend(rate):
        if rate is None or abs(float(rate)) < 0.5:
            return "level"
        return "climbing" if float(rate) > 0 else "descending"
