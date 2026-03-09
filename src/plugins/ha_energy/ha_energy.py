from plugins.base_plugin.base_plugin import BasePlugin
import requests
import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

# Home Assistant REST API endpoints
HA_STATES_URL = "{base_url}/api/states/{entity_id}"
HA_HISTORY_URL = "{base_url}/api/history/period/{start_time}?filter_entity_id={entity_id}&minimal_response&no_attributes&significant_changes_only=0"


class HAEnergy(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        ha_url = settings.get('ha_url', '').rstrip('/')
        if not ha_url:
            raise RuntimeError("Home Assistant URL is required.")

        access_token = device_config.load_env_key("HA_ACCESS_TOKEN")
        if not access_token:
            raise RuntimeError(
                "Home Assistant Access Token not configured. "
                "Add HA_ACCESS_TOKEN to your .env file."
            )

        sensor_solar = settings.get('sensor_solar', '')
        sensor_grid_import = settings.get('sensor_grid_import', '')
        sensor_grid_export = settings.get('sensor_grid_export', '')
        sensor_battery_charge = settings.get('sensor_battery_charge', '')
        sensor_battery_discharge = settings.get('sensor_battery_discharge', '')

        if not (sensor_solar and sensor_grid_import and sensor_grid_export and sensor_battery_charge and sensor_battery_discharge):
            raise RuntimeError("All 5 energy sensors (solar, grid import/export, battery charge/discharge) must be configured in settings.")
        title = settings.get('display_title', 'Energy')

        timezone_str = device_config.get_config("timezone", default="UTC")
        time_format = device_config.get_config("time_format", default="12h")
        tz = pytz.timezone(timezone_str)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            # Fetch current states for friendly names and units
            solar_data = self._fetch_entity_state(ha_url, sensor_solar, headers)
            grid_import_data = self._fetch_entity_state(ha_url, sensor_grid_import, headers)
            grid_export_data = self._fetch_entity_state(ha_url, sensor_grid_export, headers)
            batt_charge_data = self._fetch_entity_state(ha_url, sensor_battery_charge, headers)
            batt_discharge_data = self._fetch_entity_state(ha_url, sensor_battery_discharge, headers)

            show_chart = settings.get('show_chart', 'true') == 'true'
            now = datetime.now(tz)

            all_entities = {
                "solar": (sensor_solar, solar_data),
                "grid_import": (sensor_grid_import, grid_import_data),
                "grid_export": (sensor_grid_export, grid_export_data),
                "batt_charge": (sensor_battery_charge, batt_charge_data),
                "batt_discharge": (sensor_battery_discharge, batt_discharge_data),
            }

            chart_labels, chart_datasets, day_totals, pos_bars, neg_bars, chart_max, chart_unit = self._fetch_all_history(
                ha_url, all_entities, headers, tz, now
            )

            # Override the displayed values with 24h totals
            solar_data["state_24h"] = day_totals.get(sensor_solar, solar_data["state"])
            grid_import_data["state_24h"] = day_totals.get(sensor_grid_import, grid_import_data["state"])
            grid_export_data["state_24h"] = day_totals.get(sensor_grid_export, grid_export_data["state"])
            batt_charge_data["state_24h"] = day_totals.get(sensor_battery_charge, batt_charge_data["state"])
            batt_discharge_data["state_24h"] = day_totals.get(sensor_battery_discharge, batt_discharge_data["state"])

        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Could not connect to Home Assistant at {ha_url}. "
                "Please check the URL and ensure HA is running."
            )
        except requests.exceptions.Timeout:
            raise RuntimeError("Home Assistant request timed out.")
        except Exception as e:
            logger.error(f"Home Assistant request failed: {str(e)}")
            raise RuntimeError(
                "Failed to fetch data from Home Assistant. Check logs."
            )

        # Build template params
        template_params = self._build_template_params(
            solar_data, grid_import_data, grid_export_data,
            batt_charge_data, batt_discharge_data,
            chart_labels, chart_datasets, title, tz, time_format, settings,
            show_chart, pos_bars, neg_bars, chart_max, chart_unit
        )

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        template_params["plugin_settings"] = settings

        image = self.render_image(
            dimensions, "ha_energy.html", "ha_energy.css", template_params
        )

        if not image:
            raise RuntimeError("Failed to render image, please check logs.")
        return image

    def _fetch_entity_state(self, base_url, entity_id, headers):
        """Fetch the current state of a Home Assistant entity."""
        url = HA_STATES_URL.format(base_url=base_url, entity_id=entity_id)
        response = requests.get(url, headers=headers, timeout=30, verify=False)

        if response.status_code == 401:
            raise RuntimeError(
                "Authentication failed. Check your HA_ACCESS_TOKEN."
            )
        if response.status_code == 404:
            raise RuntimeError(f"Entity '{entity_id}' not found in Home Assistant.")
        if not 200 <= response.status_code < 300:
            logger.error(
                f"Failed to get state for {entity_id}: {response.content}"
            )
            raise RuntimeError(f"Failed to retrieve state for {entity_id}.")

        data = response.json()
        state = data.get("state", "unavailable")
        attributes = data.get("attributes", {})

        return {
            "entity_id": entity_id,
            "state": state,
            "state_24h": None,  # Will be computed from history
            "friendly_name": attributes.get(
                "friendly_name", entity_id.split(".")[-1].replace("_", " ").title()
            ),
            "unit": attributes.get("unit_of_measurement", ""),
            "icon": attributes.get("icon", "mdi:flash"),
            "device_class": attributes.get("device_class", ""),
        }

    def _fetch_all_history(self, base_url, all_entities, headers, tz, now):
        """Fetch 24h history for all entities. Returns chart data and 24h totals."""
        start_time = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S%z")

        # Build 24-hour label array
        labels = []
        for i in range(24):
            hour_dt = now - timedelta(hours=23 - i)
            labels.append(hour_dt.strftime("%H:00"))

        datasets_raw = {}
        day_totals = {}

        for role, (entity_id, entity_data) in all_entities.items():
            url = HA_HISTORY_URL.format(
                base_url=base_url, start_time=start_time, entity_id=entity_id
            )
            try:
                response = requests.get(url, headers=headers, timeout=30, verify=False)
                if not 200 <= response.status_code < 300:
                    logger.warning(f"Failed to get history for {entity_id}: {response.status_code}")
                    datasets_raw[role] = [0] * 24
                    continue

                history = response.json()
                if not history or not history[0]:
                    logger.warning(f"No history data for {entity_id}")
                    datasets_raw[role] = [0] * 24
                    continue

                readings = self._parse_readings(history[0], tz)
                if not readings:
                    logger.warning(f"No valid readings for {entity_id}")
                    datasets_raw[role] = [0] * 24
                    continue

                # Compute 24h total: last reading - first reading
                day_totals[entity_id] = round(readings[-1][1] - readings[0][1], 2)

                # Compute per-hour deltas for chart
                datasets_raw[role] = self._compute_hourly_deltas(readings, tz, now)

            except Exception as e:
                logger.warning(f"Error fetching history for {entity_id}: {e}")
                datasets_raw[role] = [0] * 24

        # Compute calculated chart categories
        solar_raw = datasets_raw.get("solar", [0]*24)
        grid_in = datasets_raw.get("grid_import", [0]*24)
        grid_out = datasets_raw.get("grid_export", [0]*24)
        batt_in = datasets_raw.get("batt_charge", [0]*24)
        batt_out = datasets_raw.get("batt_discharge", [0]*24)

        solar_direct = []
        for h in range(24):
            # Solar direct used = total solar - what went to grid - what went to battery
            val = solar_raw[h] - grid_out[h] - batt_in[h]
            solar_direct.append(max(0, val)) # avoid negative due to interpolation anomalies

        # Chart dataset configuration (positive and negative)
        # Colors: red, green, blue, yellow, black, white
        
        pos_series = [
            {"key": "solar_direct", "data": solar_direct, "label": "Solar Use", "color": "yellow"},
            {"key": "batt_out", "data": batt_out, "label": "Battery Use", "color": "green"},
            {"key": "grid_in", "data": grid_in, "label": "Grid Use", "color": "red"}
        ]

        neg_series = [
            {"key": "batt_in", "data": batt_in, "label": "Batt. Charge", "color": "blue"},
            {"key": "grid_out", "data": grid_out, "label": "Grid Export", "color": "black"}
        ]

        # Calculate max scale for percentage heights
        chart_max = 0
        for h in range(24):
            # We want both axes to scale identically so the 0 line is central
            pos_sum = sum(s["data"][h] for s in pos_series)
            neg_sum = sum(s["data"][h] for s in neg_series)
            chart_max = max(chart_max, pos_sum, neg_sum)

        if chart_max == 0:
            chart_max = 1.0

        chart_unit = "Wh"
        scale_factor = 1.0
        if chart_max > 1000.0:
            scale_factor = 1000.0
            chart_unit = "kWh"
            chart_max = chart_max / 1000.0

        pos_bars = []
        neg_bars = []
        for h in range(24):
            pbar = {"label": labels[h], "segments": []}
            for s in pos_series:
                val = s["data"][h]
                if val > 0:
                    pct = (val / (chart_max * scale_factor)) * 100
                    pbar["segments"].append({
                        "color": s["color"],
                        "pct": pct,
                        "value": round(val/scale_factor, 2)
                    })
            pos_bars.append(pbar)

            nbar = {"label": labels[h], "segments": []}
            for s in neg_series:
                val = s["data"][h]
                if val > 0:
                    pct = (val / (chart_max * scale_factor)) * 100
                    nbar["segments"].append({
                        "color": s["color"],
                        "pct": pct,
                        "value": round(val/scale_factor, 2)
                    })
            neg_bars.append(nbar)

        chart_datasets = []
        for s in pos_series:
            chart_datasets.append({"label": s["label"], "bg_color": s["color"]})
        for s in neg_series:
            chart_datasets.append({"label": s["label"], "bg_color": s["color"]})

        return labels, chart_datasets, day_totals, pos_bars, neg_bars, round(chart_max, 1), chart_unit

    def _parse_readings(self, entries, tz):
        """Parse history entries into sorted (datetime, value) list."""
        readings = []
        for entry in entries:
            state = entry.get("state", "")
            try:
                value = float(state)
            except (ValueError, TypeError):
                continue

            last_changed = entry.get("last_changed", "")
            if not last_changed:
                continue

            try:
                dt = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
                dt = dt.astimezone(tz)
            except (ValueError, TypeError):
                continue

            readings.append((dt, value))

        readings.sort(key=lambda x: x[0])
        return readings

    def _compute_hourly_deltas(self, readings, tz, now):
        """
        Compute per-hour energy consumption from sorted cumulative readings.

        For each hour, we interpolate the value at the hour boundary and
        compute the delta. This handles sparse readings correctly.
        """
        if not readings:
            return [0] * 24

        # Build sorted list of hour boundaries
        hour_boundaries = []
        for i in range(25):  # 25 boundaries for 24 gaps
            hour_dt = (now - timedelta(hours=24 - i)).replace(
                minute=0, second=0, microsecond=0
            )
            hour_boundaries.append(hour_dt)

        # Get the value at each hour boundary by interpolation
        boundary_values = []
        for boundary in hour_boundaries:
            val = self._interpolate_value(readings, boundary)
            boundary_values.append(val)

        logger.info(
            f"Boundary values (first 5): "
            f"{boundary_values[0:5]}, ..., (last 3): {boundary_values[-3:]}"
        )

        # Compute deltas between consecutive boundaries
        result = []
        for i in range(24):
            val1 = boundary_values[i]
            val2 = boundary_values[i + 1]
            if val1 is not None and val2 is not None:
                delta = val2 - val1
                result.append(round(max(delta, 0), 2))
            else:
                result.append(0)

        logger.info(f"Hourly deltas: {result}")
        return result

    def _interpolate_value(self, readings, target_dt):
        """
        Find the value at a specific datetime by using the last known reading
        before that time (step interpolation, matching HA behavior).
        """
        last_val = None
        for dt, val in readings:
            if dt <= target_dt:
                last_val = val
            else:
                break
        return last_val

    def _build_template_params(
        self, solar, grid_in, grid_out, batt_in, batt_out,
        chart_labels, chart_datasets, title, tz, time_format, settings,
        show_chart, pos_bars, neg_bars, chart_max, chart_unit
    ):
        """Build the template parameters for rendering."""
        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh = now.strftime("%Y-%m-%d %H:%M")
        else:
            last_refresh = now.strftime("%Y-%m-%d %I:%M %p")

        # Utility to get value safely
        def get_val(data_dict):
            state = data_dict.get("state_24h", data_dict.get("state"))
            try:
                return f"{float(state):.1f}"
            except (ValueError, TypeError):
                return str(state)

        params = {
            "title": title,
            "last_refresh_time": last_refresh,
            "show_metrics": settings.get("show_metrics", "true") == "true",
            "show_refresh_time": settings.get("show_refresh_time", "true") == "true",
            
            # Use top value boxes for Solar, Grid Import, Grid Export, Battery? 
            # We have 3 boxes on UI currently. Let's do: Solar, Grid In, Grid Out
            "primary_name": "Total Solar",
            "primary_value": get_val(solar),
            "primary_unit": solar["unit"],
            
            "has_secondary": True,
            "secondary_name": "Grid In",
            "secondary_value": get_val(grid_in),
            "secondary_unit": grid_in["unit"],
            
            "has_tertiary": True,
            "tertiary_name": "Grid Out",
            "tertiary_value": get_val(grid_out),
            "tertiary_unit": grid_out["unit"],

            "chart_labels": chart_labels,
            "chart_datasets": chart_datasets,
            "pos_bars": pos_bars,
            "neg_bars": neg_bars,
            "chart_max": chart_max,
            "chart_unit": chart_unit,
            "show_chart": show_chart,
        }

        return params
