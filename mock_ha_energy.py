import os
import io
import sys
sys.path.append("/home/tobias/Projects/InkyPi/src")
from unittest.mock import patch, MagicMock
from plugins.ha_energy.ha_energy import HAEnergy
from config import Config as DeviceConfig

# We need a mock configuration
class MockDeviceConfig(DeviceConfig):
    def __init__(self):
        # Config subclass requires empty __init__ args
        self.config = {
            "timezone": "Europe/Berlin",
            "time_format": "24h",
            "orientation": "horizontal",
            "resolution": [800, 480]
        }
    def load_env_key(self, key):
        if key == "HA_ACCESS_TOKEN":
            return "MOCK_TOKEN"
        return None
    def get_resolution(self):
        return (800, 480) # typical display size

def mock_get(url, *args, **kwargs):
    mock = MagicMock()
    mock.status_code = 200
    
    if "api/states/" in url:
        mock.json.return_value = {
            "state": "100.0",
            "attributes": {
                "friendly_name": "Mock Sensor",
                "unit_of_measurement": "kWh"
            }
        }
    elif "api/history/" in url:
        # Generate some mock history data depending on entity id in URL
        # Format: [{"state": "val", "last_changed": "2023-01-01T00:00:00Z"}, ...]
        from datetime import datetime, timedelta
        import random
        
        now = datetime.utcnow()
        readings = []
        base_val = 1000.0
        
        for i in range(25):
            dt = now - timedelta(hours=24-i)
            # Add some random delta based on the sensor type
            if "solar" in url:
                if 2 <= i <= 8 and i < 20: # daytime
                    base_val += random.uniform(0, 3)
            elif "grid_import" in url:
                base_val += random.uniform(0.1, 1.5)
            elif "grid_export" in url:
                base_val += random.uniform(0, 1.5)
            elif "battery_charge" in url:
                base_val += random.uniform(0, 1)
            elif "battery_discharge" in url:
                base_val += random.uniform(0, 1)
                
            readings.append({
                "state": str(base_val),
                "last_changed": dt.isoformat() + "Z"
            })
            
        mock.json.return_value = [readings]
    else:
        mock.json.return_value = {}
        
    return mock

@patch('requests.get', side_effect=mock_get)
def test_plugin(mock_get_func=None):
    device_config = MockDeviceConfig()
    # BasePlugin expects config which will have id
    plugin_config = {"id":"ha_energy"}
    plugin = HAEnergy(config=plugin_config)
    settings = {
        "ha_url": "http://mock-ha",
        "sensor_solar": "sensor.solar",
        "sensor_grid_import": "sensor.grid_import",
        "sensor_grid_export": "sensor.grid_export",
        "sensor_battery_charge": "sensor.battery_charge",
        "sensor_battery_discharge": "sensor.battery_discharge",
        "display_title": "Energy Mock",
        "show_chart": "true"
    }
    
    # We may need to mock render_image if we just want to see the template params
    original_render = plugin.render_image
    
    def hooked_render(dimensions, template_file, css_file, template_params):
        print("Template Params generated successfully!")
        print("Max chart value:", template_params.get("chart_max"))
        print("Number of pos_bars:", len(template_params.get("pos_bars", [])))
        print("Number of neg_bars:", len(template_params.get("neg_bars", [])))
        
        # Now run actual render and save it
        img = original_render(dimensions, template_file, css_file, template_params)
        if img:
            with open("/tmp/test_ha_energy.png", "wb") as f:
                img.save(f, format="PNG")
            print("Saved image to /tmp/test_ha_energy.png")
        return img
        
    with patch.object(plugin, 'render_image', side_effect=hooked_render):
        plugin.generate_image(settings, device_config)

if __name__ == "__main__":
    os.environ["DISPLAY"] = ":0" # In case playwright complains
    test_plugin()
