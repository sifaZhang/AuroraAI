from __future__ import annotations

from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING_PATH = PROJECT_ROOT / "config" / "research_type_mapping.yaml"


def load_mapping(path: Path = DEFAULT_MAPPING_PATH) -> dict[str, dict[str, dict[str, str | None]]]:
    if not path.exists():
        return {"by_sw_l3": {}, "overrides": {}}
    # Deliberately small YAML subset: top-level section, key, then scalar fields.
    # It keeps this file dependency-free while accepting the documented mapping shape.
    result: dict[str, dict[str, dict[str, str | None]]] = {"by_sw_l3": {}, "overrides": {}}
    section: str | None = None
    key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section, key = line[:-1], None
            if section not in result:
                raise ValueError(f"unsupported mapping section: {section}")
        elif line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            if section is None:
                raise ValueError("mapping key without section")
            key = line.strip()[:-1]
            result[section][key] = {}
        elif line.startswith("    ") and ":" in line and section and key:
            field, value = line.strip().split(":", 1)
            if field not in {"primary", "secondary"}:
                raise ValueError(f"unsupported mapping field: {field}")
            result[section][key][field] = value.strip().strip("\"'") or None
        else:
            raise ValueError(f"invalid research type mapping line: {raw_line}")
    return result


def resolve_research_type(symbol: str, sw_l3_name: str | None, mapping: dict[str, Any]) -> dict[str, str | None]:
    value = mapping.get("overrides", {}).get(symbol.upper())
    source = "symbol_override"
    if value is None:
        value = mapping.get("by_sw_l3", {}).get(sw_l3_name or "")
        source = "sw_l3_mapping"
    if value is None:
        return {"primary": None, "secondary": None, "source": "unresolved"}
    return {"primary": value.get("primary"), "secondary": value.get("secondary"), "source": source}
