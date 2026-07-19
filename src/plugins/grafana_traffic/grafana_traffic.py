from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import requests

from plugins.base_plugin.base_plugin import BasePlugin

logger = logging.getLogger(__name__)
QUERY = 'sum(rate({job="traefik-access", host!~".*\\\\.(home|localhost)$", host!=""}[5m])) * 60'


class GrafanaTraffic(BasePlugin):
    def generate_image(self, settings, device_config):
        grafana_url = settings.get("grafana_url", "").rstrip("/")
        if not grafana_url:
            raise RuntimeError("Grafana URL is required.")
        token = device_config.load_env_key("GRAFANA_API_TOKEN")
        if not token:
            raise RuntimeError("Add GRAFANA_API_TOKEN to InkyPi's .env file.")

        timezone_name = device_config.get_config("timezone", default="UTC")
        timezone = ZoneInfo(timezone_name)
        end = datetime.now(timezone)
        start = end - timedelta(hours=24)
        points = self._fetch(grafana_url, token, start, end, settings.get("verify_tls", "false") == "true")
        params = self._chart(points, start, end)
        params.update({
            "title": settings.get("display_title", "EXTERNAL TRAFFIC · 24 HOURS"),
            "updated": end.strftime("%H:%M"),
            "timezone": timezone_name,
            "plugin_settings": settings,
        })
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        image = self.render_image(dimensions, "grafana_traffic.html", "grafana_traffic.css", params)
        if not image:
            raise RuntimeError("Failed to render Grafana traffic image.")
        return image

    def _fetch(self, base_url, token, start, end, verify_tls):
        payload = {
            "from": str(int(start.timestamp() * 1000)),
            "to": str(int(end.timestamp() * 1000)),
            "queries": [{
                "refId": "A", "datasource": {"type": "loki", "uid": "loki"},
                "editorMode": "code", "expr": QUERY,
                "legendFormat": "External requests/min", "queryType": "range", "maxLines": 0,
            }],
        }
        try:
            response = requests.post(
                f"{base_url}/api/ds/query",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload, timeout=30, verify=verify_tls,
            )
        except requests.exceptions.ConnectionError as error:
            raise RuntimeError(f"Could not connect to Grafana at {base_url}.") from error
        except requests.exceptions.Timeout as error:
            raise RuntimeError("Grafana request timed out.") from error
        if response.status_code in (401, 403):
            raise RuntimeError("Grafana authentication failed. Check GRAFANA_API_TOKEN.")
        response.raise_for_status()
        frames = response.json().get("results", {}).get("A", {}).get("frames", [])
        if not frames:
            return []
        frame = frames[0]
        fields = frame.get("schema", {}).get("fields", [])
        values = frame.get("data", {}).get("values", [])
        ti = next((i for i, field in enumerate(fields) if field.get("type") == "time"), 0)
        vi = next((i for i, field in enumerate(fields) if field.get("type") == "number"), 1)
        if ti >= len(values) or vi >= len(values):
            return []
        points = []
        for timestamp, value in zip(values[ti], values[vi]):
            try:
                points.append((float(timestamp) / 1000, max(0, float(value))))
            except (TypeError, ValueError):
                continue
        return points

    def _chart(self, points, start, end):
        plot = {"left": 74, "top": 92, "right": 770, "bottom": 400}
        values = [value for _, value in points]
        observed_max = max([1, *values])
        chart_max = observed_max * 1.12
        span = max(1, end.timestamp() - start.timestamp())
        x = lambda timestamp: plot["left"] + max(0, min(1, (timestamp - start.timestamp()) / span)) * (plot["right"] - plot["left"])
        y = lambda value: plot["bottom"] - value / chart_max * (plot["bottom"] - plot["top"])
        coords = [f"{x(timestamp):.1f},{y(value):.1f}" for timestamp, value in points]
        polyline = " ".join(coords)
        area = (
            f'{x(points[0][0]):.1f},{plot["bottom"]} '
            f'{polyline} {x(points[-1][0]):.1f},{plot["bottom"]}'
            if coords else ""
        )
        grid = []
        for fraction in (0, .25, .5, .75, 1):
            grid.append({
                "y": plot["bottom"] - fraction * (plot["bottom"] - plot["top"]),
                "label": self._number(chart_max * fraction),
            })
        labels = [{
            "x": plot["left"] + hours / 24 * (plot["right"] - plot["left"]),
            "label": "now" if hours == 24 else f"−{24 - hours}h",
        } for hours in (0, 6, 12, 18, 24)]
        return {
            "plot": plot, "polyline": polyline, "area": area,
            "grid_lines": grid, "time_labels": labels, "has_data": bool(points),
            "current": self._number(values[-1] if values else 0),
            "peak": self._number(max(values) if values else 0),
        }

    @staticmethod
    def _number(value):
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        if value >= 100:
            return f"{value:.0f}"
        if value >= 10:
            return f"{value:.1f}"
        return f"{value:.2f}".rstrip("0").rstrip(".")
