"""Google Flights point-of-sale (POS) region lists.

The canonical region file is ``all_available_regions_mapped.json`` at the project
root: a list of ``{"name": ..., "gl": ...}`` objects covering every country code
Google Flights exposes as a point of sale.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import PROJECT_ROOT

REGIONS_MAPPING_FILE = PROJECT_ROOT / "all_available_regions_mapped.json"


def load_all_regions(regions_file: Path | str | None = None) -> list[dict[str, str]]:
    """Load ``[{"gl": .., "name": ..}, ..]`` region records, falling back to gl as name."""
    target = Path(regions_file) if regions_file else REGIONS_MAPPING_FILE
    if not target.exists():
        raise FileNotFoundError(f"Missing region mapping file: {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    return [{"gl": item["gl"], "name": item.get("name", item["gl"])} for item in data if "gl" in item]


def load_region_name_map(regions_file: Path | str | None = None) -> dict[str, str]:
    """Map gl country code -> human readable market name; empty dict if unavailable."""
    try:
        return {r["gl"]: r["name"] for r in load_all_regions(regions_file)}
    except (FileNotFoundError, ValueError, KeyError):
        return {}
