"""Offline regressions: measured values must retain their hardware scope."""
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import process_manager as pm


class SensorTests(unittest.TestCase):
    def sensor(self, name, parent, value, kind="Temperature", hardware_type="Cpu"):
        hw = [{"Identifier": parent, "HardwareType": hardware_type, "Name": hardware_type}]
        return pm.classify_sensor({"Name": name, "Identifier": parent + "/sensor/0", "Parent": parent,
                                   "SensorType": kind, "Value": value}, hw)

    def test_invalid_values(self):
        for value in (None, "", "NaN", "Infinity", float("-inf")):
            self.assertIsNone(pm.finite_sensor_value(value))
        self.assertEqual(pm.finite_sensor_value(0), 0)
        self.assertEqual(pm.finite_sensor_value(42.5), 42.5)

    def test_gpu_and_disk_not_cpu(self):
        records = [self.sensor("CPU Package", "/intelcpu/0", 50),
                   self.sensor("GPU Core", "/gpu-nvidia/0", 90, hardware_type="GpuNvidia"),
                   self.sensor("Temperature", "/nvme/0", 95, hardware_type="Storage")]
        result = pm.summarize_sensors(records)
        self.assertEqual(result["cpu_temperature"], 50)
        self.assertEqual(result["gpu_temperature"], 90)

    def test_no_cpu_sensor(self):
        result = pm.summarize_sensors([self.sensor("GPU Core", "/gpu-nvidia/0", 90, hardware_type="GpuNvidia")])
        self.assertIsNone(result["cpu_temperature"])

    def test_core_fallback(self):
        result = pm.summarize_sensors([self.sensor("CPU Core #1", "/intelcpu/0", 65),
                                      self.sensor("CPU Core #2", "/intelcpu/0", 70)])
        self.assertEqual(result["cpu_temperature"], 70)
        self.assertEqual(result["cpu_temperature_kind"], "cpu_core")

    def test_no_fake_whole_system_sum(self):
        records = [self.sensor("CPU Package", "/intelcpu/0", 20, "Power"),
                   self.sensor("CPU Cores", "/intelcpu/0", 10, "Power"),
                   self.sensor("GPU Power", "/gpu-nvidia/0", 15, "Power", "GpuNvidia")]
        result = pm.summarize_sensors(records)
        self.assertEqual(result["cpu_package_power"], 20)
        self.assertIsNone(result["system_power"])

    def test_explicit_system_power(self):
        record = self.sensor("Total System Power", "/motherboard/0", 55, "Power", "Motherboard")
        self.assertEqual(pm.summarize_sensors([record])["system_power"], 55)

    def test_battery_discharge_not_system_input(self):
        record = self.sensor("Discharge Rate", "/battery/0", 25, "Power", "Battery")
        self.assertIsNone(pm.summarize_sensors([record])["system_power"])

    def test_unknown_temperature_not_cpu(self):
        record = self.sensor("Temperature", "/unknown/0", 85, hardware_type="Unknown")
        self.assertIsNone(pm.summarize_sensors([record])["cpu_temperature"])

    def test_acpi_never_becomes_cpu(self):
        app = SimpleNamespace(cpu_percent=50, sensor_records=[], thermal_hist=[], refresh_thermal_view=lambda: None)
        app._judge_thermal = lambda: pm.App._judge_thermal(app)
        with patch.object(pm, "read_bridge_sensors", return_value=[]):
            pm.App._parse_counters(app, "TZ|TZ00|301\nHP|TZ00|3010\n")
        self.assertIsNone(app.temp_cpu)
        self.assertAlmostEqual(app.zone_temps["TZ00"], 27.85)

    def test_cpu_platform_is_not_wall_power(self):
        record = self.sensor("CPU Platform", "/intelcpu/0", 40, "Power")
        self.assertIsNone(pm.summarize_sensors([record])["system_power"])

    def test_bridge_expired_or_invalid_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "bridge.ps1"
            script.write_text("# local bridge", encoding="utf-8")
            snapshot = root / "snapshot.json"
            (root / "hardware_sensor_bridge.json").write_text(json.dumps({"script_path":str(script), "snapshot_path":str(snapshot)}),encoding="utf-8")
            payload = {"Timestamp":time.time(), "Source":"LHM official library", "Hardware":[{"Identifier":"/intelcpu/0", "HardwareType":"Cpu"}], "Sensors":[{"Name":"CPU Package", "Identifier":"/intelcpu/0/temperature/0", "Parent":"/intelcpu/0", "SensorType":"Temperature", "Value":64}]}
            snapshot.write_text(json.dumps(payload),encoding="utf-8")
            self.assertEqual(pm.summarize_sensors(pm.read_bridge_sensors(directory))["cpu_temperature"],64)
            payload["Timestamp"] = time.time()-20
            snapshot.write_text(json.dumps(payload),encoding="utf-8")
            self.assertEqual(pm.read_bridge_sensors(directory),[])
            snapshot.write_text("{partial",encoding="utf-8")
            self.assertEqual(pm.read_bridge_sensors(directory),[])

    def test_empty_refresh_clears_values(self):
        result = pm.summarize_sensors([])
        for key in ("cpu_temperature", "gpu_temperature", "system_power", "cpu_package_power"):
            self.assertIsNone(result[key])


if __name__ == "__main__":
    unittest.main()
