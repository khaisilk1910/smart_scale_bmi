"""Offline Marketplace packaging and translation checks."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "smart_scale_bmi"


def test_marketplace_manifest():
    metadata = json.loads((ROOT / "hacs.json").read_text())
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert metadata["homeassistant"] == "2026.11.0"
    assert manifest["version"] == "2026.11.0"
    assert manifest["domain"] == COMPONENT.name
    assert manifest["integration_type"] == "helper"
    assert manifest["iot_class"] == "calculated"
    assert manifest["config_flow"] is True
    assert (COMPONENT / "frontend" / "smart-scale-bmi-card.js").exists()
    assert (COMPONENT / "brand" / "icon.png").exists()
    assert not (ROOT / ".github ").exists()


def test_translations_and_actions():
    en = json.loads((COMPONENT / "translations" / "en.json").read_text())
    vi = json.loads((COMPONENT / "translations" / "vi.json").read_text())
    assert set(en["config"]["step"]["user"]["data"]) == set(vi["config"]["step"]["user"]["data"])
    assert "future_birth" in en["config"]["error"]
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    assert set(services) == {"add_measurement", "delete_measurement", "update_measurement"}
    assert all(service["fields"]["entry_id"]["selector"]["config_entry"]["integration"] == "smart_scale_bmi" for service in services.values())
    assert services["add_measurement"]["fields"]["measured_at"]["selector"] == {"datetime": {}}
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert yaml.safe_load(workflow.read_text())["jobs"]
