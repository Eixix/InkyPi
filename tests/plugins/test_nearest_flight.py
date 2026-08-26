import math
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("value", [0, 251, "oops"])
def test_radius_validation(value):
    with pytest.raises(RuntimeError):
        NearestFlight._radius(value)
