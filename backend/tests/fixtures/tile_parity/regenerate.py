"""Regenerate tile-parity fixtures from the current Python packer.

Usage (from backend/):
    .venv/bin/python tests/fixtures/tile_parity/regenerate.py

Reads cases.json, re-runs pack_surface on each case's input, and rewrites the
file with updated `expected` blocks. Commit the diff if intentional — this
is a breaking change for the Swift port.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.activities.tile_pack import pack_surface
from app.models.contracts import MaterialSpec, StartingPointRule, SurfacePatch

FIXTURE = Path(__file__).parent / "cases.json"


def _pack(case: dict) -> dict:
    patch = SurfacePatch.model_validate(case["input"]["patch"])
    spec = MaterialSpec.model_validate(case["input"]["spec"])
    rule = StartingPointRule(case["input"]["rule"])
    result = pack_surface(patch, spec, rule)
    return json.loads(result.model_dump_json())


def main() -> None:
    data = json.loads(FIXTURE.read_text())
    for case in data["cases"]:
        case["expected"] = _pack(case)
    FIXTURE.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Regenerated {len(data['cases'])} cases in {FIXTURE}")


if __name__ == "__main__":
    main()
