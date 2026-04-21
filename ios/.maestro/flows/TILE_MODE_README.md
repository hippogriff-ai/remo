# Tile-mode Maestro flows — PR 2–5 acceptance contract

These flows (`tile-*.yaml`, `happy-path-tile.yaml`) are scaffolded ahead of the
iOS implementation. They **will fail** on `main` until the matching SwiftUI
views ship in PR 2–5 — that's the point: they define the accessibility-ID
and screen-copy contract that each follow-up PR must satisfy.

Per-PR green-gate:

| PR | Must make these flows pass                                |
|----|-----------------------------------------------------------|
| 2  | `tile-01-create-project.yaml`                             |
| 3  | `tile-02-scan.yaml`, `tile-03-mask-editor.yaml`, `tile-04-specs.yaml`, `tile-09-cancel.yaml` |
| 4  | `tile-05-estimate-live-toggle.yaml`                       |
| 5  | `tile-06-render.yaml`, `tile-07-retry-render.yaml`, `tile-08-export-pdf.yaml`, `happy-path-tile.yaml` |

## Accessibility-ID contract (source of truth)

All IDs below are contracts — **do not rename without updating flows first**.

### HomeScreen (PR 2)
- `home_new_tile_project` — "Replace Material" CTA button
- `tile_project_0` — first tile-project row in the project list

### TileScan screen (PR 3)
- `tile_scan_start` — start LiDAR button
- `tile_surface_list` — surface list container
- `tile_surface_0` — first surface row (tappable → mask editor)

### Mask editor (PR 3)
- `tile_mask_canvas` — drawable canvas
- `tile_mask_add` — confirm-drawn-mask button
- `tile_tile_count_wall` / `tile_tile_count_wall_new` — wall tile count labels

### Specs form (PR 3)
- `tile_wall_width`, `tile_wall_height`, `tile_wall_grout`, `tile_wall_per_box`
- `tile_floor_width`, `tile_floor_height`, `tile_floor_grout`, `tile_floor_per_box`
- `tile_specs_continue` — submit button

### Estimate screen (PR 4)
- `tile_overage_flat_10`, `tile_overage_risk_adjusted`, `tile_overage_contractor_tier`
- `tile_tier_apprentice`, `tile_tier_pro`, `tile_tier_perfectionist`
- `tile_starting_rule_centered_focal_wall`, `tile_starting_rule_largest_wall_corner`, `tile_starting_rule_minimize_cut_count`
- `tile_wall_boxes_value`, `tile_floor_boxes_value`
- `tile_estimate_loading` — spinner (**MUST NOT** appear during policy toggle)
- `tile_estimate_confirm` — confirm button

### Render screen (PR 5)
- `tile_render_start` — begin render
- `tile_render_image` — AsyncImage for the render
- `tile_render_retry` — visible on failure
- `tile_render_error_message` — user-facing error copy

### Export screen (PR 5)
- `tile_export_button` — triggers POST `/tile-projects/{id}/export`
- `tile_export_pdf_image` — PDF preview thumbnail

### Cancel controls (PR 2+)
- `tile_cancel_button`, `tile_cancel_confirm`

## Launch args the iOS app must honor

| Arg                      | Who sets it                           | What it does |
|--------------------------|---------------------------------------|--------------|
| `maestro-test=true`      | all tile flows                        | skip splash / onboarding |
| `lidar-fixture=reference_room` | scan flows                      | inject RoomPlan JSON from `ios/.maestro/fixtures/reference_room.json` instead of running real RoomPlan |
| `force-render-failure=true`    | `tile-07-retry-render.yaml`     | render activity returns error on first attempt (test the retry path) |

## Running locally

```bash
# Single subflow
maestro test ios/.maestro/flows/tile-04-specs.yaml

# Full happy path (tile)
maestro test ios/.maestro/flows/happy-path-tile.yaml
```

## Parity with backend

Every flow is paired with backend assertions in
`backend/tests/test_api_tile_projects.py::TestAPIAgainstWorkflow`. If a flow
fails in CI, check the corresponding API test first — backend contract changes
should surface there before UI.
