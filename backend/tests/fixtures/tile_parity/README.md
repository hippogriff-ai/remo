# Python ↔ Swift parity fixtures — `pack_surface`

These fixtures exist for exactly one reason: **the Swift port of
`pack_surface` in `RemoTileMode` (PR 4) must produce the same output as the
Python canonical implementation, tile-for-tile**. Without this, the <16ms
live-toggle on EstimateScreen will drift silently from the server-side
cut-sheet math, and users will see one count on-device and a different one
in the generated PDF.

## Layout

- `cases.json` — array of test cases. Each entry has:
  - `name` — human-readable label
  - `input.patch` — `SurfacePatch` JSON (meters, surface-local)
  - `input.spec` — `MaterialSpec` JSON
  - `input.rule` — one of the three `StartingPointRule` values
  - `expected` — canonical `PackResult` JSON (produced by the Python impl)

- `regenerate.py` — run this any time the Python packer changes:
  ```bash
  cd backend
  .venv/bin/python tests/fixtures/tile_parity/regenerate.py
  ```
  The script re-runs `pack_surface` on each case's input and overwrites
  the `expected` field. Commit the diff; this is a breaking change for
  iOS. PR-level review should confirm the diff is intentional.

## How Python tests use it

`test_tile_pack.py::test_parity_fixtures_match_current_python_output` loads
each case, runs `pack_surface`, and deep-equals the result against the
fixture. If you edit the packer, this test fails → regenerate → commit.

## How Swift tests will use it (PR 4)

```swift
// ios/Packages/RemoTileMode/Tests/RemoTileModeTests/ParityTests.swift
let cases = try ParityFixture.load("tile_parity/cases.json")
for c in cases {
    let got = PackSurface.pack(patch: c.input.patch, spec: c.input.spec, rule: c.input.rule)
    XCTAssertEqual(got, c.expected, "parity failure: \(c.name)")
}
```

The Swift test target should symlink or copy this directory at build time
(xcodegen `resourcePath` entry in `ios/project.yml` — add in PR 2).

## Case coverage (intentional set)

1. Exact-fit rectangular wall (`12 tiles`, no cuts)
2. Inexact-fit wall producing edge cuts
3. Wall with a door opening (tiles fully inside the opening skipped)
4. Wall with a mask hole (identical to opening — covers `HoleKind` parity)
5. Centered-focal-wall start (origin offset negative)
6. Largest-wall-corner start
7. Minimize-cut-count start (deterministic tie-breaker required)
8. Large-format tile (0.6 × 1.2 m) with 5 mm grout
9. Irregular pentagon polygon (bbox-packed in PR 1; update when PR 4
   clips to polygon)
10. Floor patch with 60 × 60 cm tile

Add cases when a bug surfaces. **Never remove cases.**
