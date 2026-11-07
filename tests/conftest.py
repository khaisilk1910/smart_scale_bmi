"""Isolated pure-logic tests without installing the full Home Assistant runtime.

The actual integration APIs must additionally be exercised on a running HA instance.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# Load the package modules without executing custom_components/.../__init__.py,
# which imports Home Assistant core services and the frontend.
base = Path(__file__).resolve().parents[1] / "custom_components" / "smart_scale_bmi"
package = types.ModuleType("smart_scale_bmi")
package.__path__ = [str(base)]
sys.modules.setdefault("smart_scale_bmi", package)

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
ha_const = types.ModuleType("homeassistant.const")
ha_const.Platform = types.SimpleNamespace(SENSOR="sensor")
ha_config_entries = types.ModuleType("homeassistant.config_entries")
ha_config_entries.ConfigEntry = object
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.const", ha_const)
sys.modules.setdefault("homeassistant.config_entries", ha_config_entries)
