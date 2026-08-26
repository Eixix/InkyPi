import math
import sys
from pathlib import Path

import pytest
import requests
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from plugins.nearest_flight.nearest_flight import NearestFlight


def test_distance_and_bearing():
    assert NearestFlight._distance_km(52.52, 13.405, 52.52, 13.405) == 0
    assert math.isclose(NearestFlight._distance_km(52.52, 13.405, 52.52, 14.405), 67.7, abs_tol=0.5)
    assert NearestFlight._compass_direction(NearestFlight._bearing(0, 0, 0, 1)) == "E"


def test_parse_state_uses_callsign_and_converts_units():
    state = ["abc123", " TEST42 ", "Germany", None, None, 13.5, 52.6, 1000, False, 100, 91, 2, None, 1200]
    aircraft = NearestFlight._parse_state(state, 52.52, 13.405)
    assert aircraft["callsign"] == "TEST42"
    assert aircraft["icao24"] == "ABC123"
    assert aircraft["altitude_m"] == 1200
    assert aircraft["speed_kmh"] == 360
    assert aircraft["heading_arrow"] == "→"
    assert aircraft["vertical_trend"] == "climbing"


def test_grounded_and_positionless_states_are_ignored():
    grounded = ["abc", "X", "DE", None, None, 13.5, 52.6, 100, True, 0, 0, 0, None, 100]
    assert NearestFlight._parse_state(grounded, 52, 13) is None
    grounded[8] = False
    grounded[5] = None
    assert NearestFlight._parse_state(grounded, 52, 13) is None


@patch("plugins.nearest_flight.nearest_flight.get_http_session")
def test_enrichment_adds_route_and_aircraft_details(get_session):
    response = Mock(status_code=200)
    response.json.return_value = {"response": {
        "aircraft": {
            "registration": "D-AIXA", "manufacturer": "Airbus",
            "type": "A350-900", "icao_type": "A359",
            "registered_owner": "Lufthansa"
        },
        "flightroute": {
            "airline": {"name": "Lufthansa"},
            "origin": {"iata_code": "FRA", "icao_code": "EDDF", "municipality": "Frankfurt"},
            "destination": {"iata_code": "JFK", "icao_code": "KJFK", "municipality": "New York"}
        }
    }}
    get_session.return_value.get.return_value = response
    aircraft = {"icao24": "3C64F0", "callsign": "DLH400"}

    NearestFlight({"id": "nearest_flight"})._enrich_aircraft(aircraft)

    assert aircraft["registration"] == "D-AIXA"
    assert aircraft["model"] == "A350-900"
    assert aircraft["operator"] == "Lufthansa"
    assert aircraft["origin"] == {"code": "FRA", "name": "Frankfurt"}
    assert aircraft["destination"] == {"code": "JFK", "name": "New York"}


@patch("plugins.nearest_flight.nearest_flight.get_http_session")
def test_enrichment_failure_keeps_live_aircraft_usable(get_session):
    get_session.return_value.get.side_effect = requests.Timeout
    aircraft = {"icao24": "ABC123", "callsign": "TEST42"}
    NearestFlight({"id": "nearest_flight"})._enrich_aircraft(aircraft)
    assert aircraft == {"icao24": "ABC123", "callsign": "TEST42"}


def test_route_progress_uses_relative_distance_to_both_endpoints():
    aircraft = {
        "origin": {"latitude": 0, "longitude": 0},
        "destination": {"latitude": 0, "longitude": 10},
        "latitude": 0, "longitude": 5,
    }
    assert NearestFlight._route_progress(aircraft) == 50
    aircraft["longitude"] = 9
    assert NearestFlight._route_progress(aircraft) == 90


def test_same_airport_route_is_rejected():
    origin = {"code": "LHR", "latitude": 51.47, "longitude": -0.45}
    assert NearestFlight._same_airport(origin, {"code": "LHR"}) is True
    assert NearestFlight._same_airport(origin, {"code": "JFK"}) is False


def test_display_facing_correction_rotates_relative_arrow():
    # An aircraft east of the location is straight ahead when the display faces east.
    relative_bearing = (90 - 90) % 360
    assert NearestFlight._heading_arrow(relative_bearing) == "↑"


@pytest.mark.parametrize("value", [0, 251, "oops"])
def test_radius_validation(value):
    with pytest.raises(RuntimeError):
        NearestFlight._radius(value)
