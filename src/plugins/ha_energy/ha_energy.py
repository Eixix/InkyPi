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

        primary_entity = settings.get('primary_entity', '')
        if not primary_entity:
            raise RuntimeError("Primary energy entity ID is required.")

        secondary_entity = settings.get('secondary_entity', '')
        tertiary_entity = settings.get('tertiary_entity', '')
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
            primary_data = self._fetch_entity_state(ha_url, primary_entity, headers)
            secondary_data = None
            tertiary_data = None

            if secondary_entity:
                secondary_data = self._fetch_entity_state(
                    ha_url, secondary_entity, headers
                )
            if tertiary_entity:
                tertiary_data = self._fetch_entity_state(
                    ha_url, tertiary_entity, headers
                )

            # Fetch 24h history for all entities
            # This gives us both: the chart data AND the 24h totals
            show_chart = settings.get('show_chart', 'true') == 'true'
            now = datetime.now(tz)

            all_entities = [
                (primary_entity, primary_data),
            ]
            if secondary_entity and secondary_data:
                all_entities.append((secondary_entity, secondary_data))
            if tertiary_entity and tertiary_data:
                all_entities.append((tertiary_entity, tertiary_data))

            chart_labels, chart_datasets, day_totals, bars, chart_max, chart_unit = self._fetch_all_history(
                ha_url, all_entities, headers, tz, now
            )

            # Override the displayed values with 24h totals
            if primary_entity in day_totals:
                primary_data["state_24h"] = day_totals[primary_entity]
            if secondary_entity and secondary_entity in day_totals:
                secondary_data["state_24h"] = day_totals[secondary_entity]
            if tertiary_entity and tertiary_entity in day_totals:
                tertiary_data["state_24h"] = day_totals[tertiary_entity]

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
            primary_data, secondary_data, tertiary_data,
            chart_labels, chart_datasets, title, tz, time_format, settings,
            show_chart, bars, chart_max, chart_unit
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

        # Chart colors for each dataset
        colors = [
            {"border": "rgba(76, 175, 80, 0.9)", "bg": "rgba(76, 175, 80, 0.45)"},   # green
            {"border": "rgba(255, 183, 77, 0.9)", "bg": "rgba(255, 183, 77, 0.45)"},  # amber
            {"border": "rgba(66, 165, 245, 0.9)", "bg": "rgba(66, 165, 245, 0.45)"},  # blue
        ]

        datasets = []
        day_totals = {}

        for idx, (entity_id, entity_data) in enumerate(all_entities):
            url = HA_HISTORY_URL.format(
                base_url=base_url, start_time=start_time, entity_id=entity_id
            )
            try:
                response = requests.get(url, headers=headers, timeout=30, verify=False)
                if not 200 <= response.status_code < 300:
                    logger.warning(f"Failed to get history for {entity_id}: {response.status_code}")
                    continue

                history = response.json()
                if not history or not history[0]:
                    logger.warning(f"No history data for {entity_id}")
                    continue

                readings = self._parse_readings(history[0], tz)
                if not readings:
                    logger.warning(f"No valid readings for {entity_id}")
                    continue

                logger.info(
                    f"History for {entity_id}: {len(readings)} readings, "
                    f"range {readings[0][1]:.2f} -> {readings[-1][1]:.2f}"
                )

                # Compute 24h total: last reading - first reading
                day_totals[entity_id] = round(readings[-1][1] - readings[0][1], 2)

                # Compute per-hour deltas for chart
                hourly_values = self._compute_hourly_deltas(readings, tz, now)

            except Exception as e:
                logger.warning(f"Error fetching history for {entity_id}: {e}")
                hourly_values = [0] * 24

            color = colors[idx % len(colors)]
            datasets.append({
                "label": entity_data["friendly_name"],
                "data": hourly_values,
                "border_color": color["border"],
                "bg_color": color["bg"],
            })

        # Compute stacked totals per hour and find the max for bar scaling
        max_stacked = 0
        if datasets:
            for h in range(24):
                stacked_total = sum(ds["data"][h] for ds in datasets)
                max_stacked = max(max_stacked, stacked_total)

        # Convert values to percentage heights and round values for display
        if max_stacked > 0:
            for ds in datasets:
                ds["pct"] = [
                    round((v / max_stacked) * 100, 1) for v in ds["data"]
                ]
        else:
            for ds in datasets:
                ds["pct"] = [0] * 24

        # Build bar data: list of 24 items, each with label + per-dataset info
        bars = []
        for h in range(24):
            bar = {"label": labels[h], "segments": []}
            for ds in datasets:
                bar["segments"].append({
                    "pct": ds["pct"][h],
                    "color": ds["bg_color"],
                    "border": ds["border_color"],
                    "value": ds["data"][h],
                })
            bars.append(bar)

        # Y-axis scale
        chart_max = max_stacked
        chart_unit = "Wh"
        if chart_max > 1000:
            chart_max = chart_max / 1000
            chart_unit = "kWh"
            for ds in datasets:
                ds["data"] = [round(v / 1000, 2) for v in ds["data"]]

        return labels, datasets, day_totals, bars, round(chart_max, 1), chart_unit

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
            f"{boundary_values[:5]}, ..., (last 3): {boundary_values[-3:]}"
        )

        # Compute deltas between consecutive boundaries
        result = []
        for i in range(24):
            if boundary_values[i] is not None and boundary_values[i + 1] is not None:
                delta = boundary_values[i + 1] - boundary_values[i]
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
        self, primary, secondary, tertiary,
        chart_labels, chart_datasets, title, tz, time_format, settings,
        show_chart, bars, chart_max, chart_unit
    ):
        """Build the template parameters for rendering."""
        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh = now.strftime("%Y-%m-%d %H:%M")
        else:
            last_refresh = now.strftime("%Y-%m-%d %I:%M %p")

        # Use 24h totals if available, otherwise fall back to current state
        primary_state = primary.get("state_24h")
        if primary_state is None:
            primary_state = primary["state"]
        try:
            primary_value = f"{float(primary_state):.1f}"
        except (ValueError, TypeError):
            primary_value = str(primary_state)

        params = {
            "title": title,
            "last_refresh_time": last_refresh,
            "primary_name": primary["friendly_name"],
            "primary_value": primary_value,
            "primary_unit": primary["unit"],
            "has_secondary": secondary is not None,
            "has_tertiary": tertiary is not None,
            "chart_labels": chart_labels,
            "chart_datasets": chart_datasets,
            "bars": bars,
            "chart_max": chart_max,
            "chart_unit": chart_unit,
            "show_chart": show_chart,
            "show_metrics": settings.get("show_metrics", "true") == "true",
            "show_refresh_time": settings.get(
                "show_refresh_time", "true"
            ) == "true",
        }

        if secondary:
            sec_state = secondary.get("state_24h")
            if sec_state is None:
                sec_state = secondary["state"]
            try:
                secondary_value = f"{float(sec_state):.1f}"
            except (ValueError, TypeError):
                secondary_value = str(sec_state)
            params["secondary_name"] = secondary["friendly_name"]
            params["secondary_value"] = secondary_value
            params["secondary_unit"] = secondary["unit"]

        if tertiary:
            ter_state = tertiary.get("state_24h")
            if ter_state is None:
                ter_state = tertiary["state"]
            try:
                tertiary_value = f"{float(ter_state):.1f}"
            except (ValueError, TypeError):
                tertiary_value = str(ter_state)
            params["tertiary_name"] = tertiary["friendly_name"]
            params["tertiary_value"] = tertiary_value
            params["tertiary_unit"] = tertiary["unit"]

        return params
