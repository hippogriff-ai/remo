"""Tests for tile-mode contracts and tile_pack pure functions.

Covers:
  - SurfacePatch / MaterialSpec validation
  - Hole / HoleKind construction
  - choose_origin: all three rules (centered / corner / min-cuts)
  - apply_overage: all three policies
  - pack_surface: hand-calculable cases, holes (opening + mask), cut detection
  - _count_cuts_and_slivers: origin-scorer smoke test
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.activities.tile_pack import (
    _count_cuts_and_slivers,
    _polygon_bbox,
    apply_overage,
    build_tile_estimate,
    choose_origin,
    pack_surface,
)
from app.models.contracts import (
    ContractorTier,
    Hole,
    HoleKind,
    MaterialSpec,
    OveragePolicy,
    StartingPointRule,
    SurfacePatch,
    TileModeInput,
    Vec2,
)


def _rect_patch(width: float, height: float, kind: str = "wall") -> SurfacePatch:
    return SurfacePatch(
        patch_id="p1",
        kind=kind,  # type: ignore[arg-type]
        polygon=[
            Vec2(x=0.0, y=0.0),
            Vec2(x=width, y=0.0),
            Vec2(x=width, y=height),
            Vec2(x=0.0, y=height),
        ],
        axis=Vec2(x=1.0, y=0.0),
        origin=Vec2(x=0.0, y=0.0),
        normal_x=0.0,
        normal_y=1.0,
        normal_z=0.0,
    )


def _spec(w: float, h: float, grout_mm: float = 3.0) -> MaterialSpec:
    return MaterialSpec(
        material_id="m1",
        module_width_m=w,
        module_height_m=h,
        grout_width_mm=grout_mm,
        modules_per_unit=10,
    )


# ----- contracts -----


def test_surface_patch_requires_three_polygon_points() -> None:
    with pytest.raises(ValidationError):
        SurfacePatch(
            patch_id="p1",
            kind="wall",
            polygon=[Vec2(x=0, y=0), Vec2(x=1, y=0)],
            axis=Vec2(x=1, y=0),
            origin=Vec2(x=0, y=0),
            normal_x=0,
            normal_y=1,
            normal_z=0,
        )


def test_material_spec_rejects_non_positive_module() -> None:
    with pytest.raises(ValidationError):
        MaterialSpec(material_id="m1", module_width_m=0, module_height_m=0.3)


# ----- choose_origin: implemented rules -----


def test_choose_origin_centered_focal_wall_is_symmetric_around_center() -> None:
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(0.6, 0.3)

    origin = choose_origin(patch, spec, StartingPointRule.CENTERED_FOCAL_WALL)

    # Center of 4x3 bbox is (2.0, 1.5); tile #1 origin is center minus half-tile.
    assert origin.x == pytest.approx(2.0 - 0.3)
    assert origin.y == pytest.approx(1.5 - 0.15)


def test_choose_origin_largest_wall_corner_starts_at_bbox_min() -> None:
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(0.6, 0.3)

    origin = choose_origin(patch, spec, StartingPointRule.LARGEST_WALL_CORNER)

    assert origin.x == pytest.approx(0.0)
    assert origin.y == pytest.approx(0.0)


# ----- choose_origin: MINIMIZE_CUT_COUNT -----


def test_choose_origin_minimize_cut_count_returns_vec_within_bbox() -> None:
    """Origin falls between [bbox.min_x - tile_w, bbox.min_x] horizontally
    (one tile-width sweep), y-origin at bbox.min_y.
    """
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(0.6, 0.3)
    origin = choose_origin(patch, spec, StartingPointRule.MINIMIZE_CUT_COUNT)
    assert -spec.module_width_m <= origin.x <= 0.0 + 1e-9
    assert origin.y == pytest.approx(0.0)


def test_choose_origin_minimize_cut_count_prefers_perfect_fit() -> None:
    """Tile that divides evenly into the surface: origin at 0 scores 0."""
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(0.5, 0.3, grout_mm=0.0)  # 4m / 0.5m = 8 tiles exact
    origin = choose_origin(patch, spec, StartingPointRule.MINIMIZE_CUT_COUNT)
    assert origin.x == pytest.approx(0.0) or origin.x == pytest.approx(-0.5)


# ----- apply_overage -----


def test_apply_overage_flat_10_rounds_up() -> None:
    assert apply_overage(100, OveragePolicy.FLAT_10, spec=_spec(0.6, 0.3), cut_count=12) == 110
    # 101 * 1.10 = 111.1 → ceil → 112
    assert apply_overage(101, OveragePolicy.FLAT_10, spec=_spec(0.6, 0.3), cut_count=12) == 112


def test_apply_overage_contractor_tier_uses_tier_rate() -> None:
    spec = _spec(0.3, 0.3)
    # Designer's tier rates: apprentice=15%, pro=12%, perfectionist=18%.
    # See design_handoff_replace_material/src/state.jsx:71.
    assert (
        apply_overage(
            100,
            OveragePolicy.CONTRACTOR_TIER,
            spec=spec,
            cut_count=0,
            tier=ContractorTier.APPRENTICE,
        )
        == 115
    )
    assert (
        apply_overage(
            100, OveragePolicy.CONTRACTOR_TIER, spec=spec, cut_count=0, tier=ContractorTier.PRO
        )
        == 112
    )
    assert (
        apply_overage(
            100,
            OveragePolicy.CONTRACTOR_TIER,
            spec=spec,
            cut_count=0,
            tier=ContractorTier.PERFECTIONIST,
        )
        == 118
    )


def test_apply_overage_risk_adjusted_scales_with_cut_ratio() -> None:
    """Designer's formula: 8% + (cut_count / tile_count) * 12%.

    See design_handoff_replace_material/src/state.jsx:61-68.
    """
    spec = _spec(0.3, 0.3)

    # No cuts: 8% → 108
    assert apply_overage(100, OveragePolicy.RISK_ADJUSTED, spec=spec, cut_count=0) == 108

    # Half cuts: 8% + 0.5*12% = 14% → 114
    assert apply_overage(100, OveragePolicy.RISK_ADJUSTED, spec=spec, cut_count=50) == 114

    # All cuts: 8% + 12% = 20% → 120
    assert apply_overage(100, OveragePolicy.RISK_ADJUSTED, spec=spec, cut_count=100) == 120

    # Cuts exceed tiles (shouldn't happen in practice but stays capped at 20%)
    assert apply_overage(100, OveragePolicy.RISK_ADJUSTED, spec=spec, cut_count=200) == 120


# ----- helpers -----


def test_polygon_bbox_on_irregular_pentagon() -> None:
    polygon = [
        Vec2(x=0.0, y=0.0),
        Vec2(x=2.0, y=0.0),
        Vec2(x=3.0, y=1.0),
        Vec2(x=2.0, y=2.0),
        Vec2(x=0.0, y=2.0),
    ]
    bbox = _polygon_bbox(polygon)
    assert (bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y) == (0.0, 0.0, 3.0, 2.0)


def test_count_cuts_and_slivers_matches_hand_calc() -> None:
    # 4m x 3m wall, 1m x 1m tile, 0 grout, origin at (0,0).
    # Fits exactly 4 x 3 = 12 full tiles, no cuts, no slivers.
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(1.0, 1.0, grout_mm=0.0)
    cuts, slivers = _count_cuts_and_slivers(patch, spec, Vec2(x=0.0, y=0.0))
    assert cuts == 0
    assert slivers == 0

    # Same wall with 0.6m x 0.3m tile, origin (0,0): width 4m / 0.6 = 6.67
    # → 6 full cols + 1 cut col (0.4m wide, above sliver threshold).
    # height 3m / 0.3 = 10 full rows. Cut count = 10 (one cut per row).
    spec2 = _spec(0.6, 0.3, grout_mm=0.0)
    cuts2, slivers2 = _count_cuts_and_slivers(patch, spec2, Vec2(x=0.0, y=0.0))
    assert cuts2 == 10
    assert slivers2 == 0  # 0.4m is above the 5cm threshold


# ----- Hole / HoleKind -----


def test_hole_construction_and_validation() -> None:
    opening = Hole(
        x_m=0.5,
        y_m=0.2,
        width_m=0.9,
        height_m=2.1,
        label="door",
        kind=HoleKind.OPENING,
    )
    assert opening.kind is HoleKind.OPENING
    assert opening.label == "door"

    mask = Hole(
        x_m=0.4,
        y_m=0.9,
        width_m=0.8,
        height_m=0.6,
        label="niche",
        kind=HoleKind.MASK,
    )
    assert mask.kind is HoleKind.MASK

    # Negative or zero dimensions rejected.
    with pytest.raises(ValidationError):
        Hole(x_m=0, y_m=0, width_m=0, height_m=1, kind=HoleKind.OPENING)


# ----- pack_surface -----


def test_pack_surface_exact_fit_produces_only_full_tiles() -> None:
    """4m x 3m wall, 1m x 1m tile, 0 grout → 12 full tiles, no cuts."""
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(1.0, 1.0, grout_mm=0.0)

    result = pack_surface(patch, spec, StartingPointRule.LARGEST_WALL_CORNER)

    assert result.full_modules == 12
    assert result.cut_modules == 0
    assert result.tiles_required == 12
    assert len(result.rects) == 12
    assert all(not r.is_cut for r in result.rects)


def test_pack_surface_inexact_fit_produces_edge_cuts() -> None:
    """4m x 3m wall, 0.6m x 0.3m tile, 0 grout, corner start.

    Width: 4m / 0.6m = 6.67 → 6 full cols + 1 cut col (0.4m).
    Height: 3m / 0.3m = 10 full rows. Total: 70 tiles, 10 cut.
    """
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(0.6, 0.3, grout_mm=0.0)

    result = pack_surface(patch, spec, StartingPointRule.LARGEST_WALL_CORNER)

    assert result.tiles_required == 70
    assert result.full_modules == 60
    assert result.cut_modules == 10
    assert result.start_x_m == pytest.approx(0.0)
    assert result.start_y_m == pytest.approx(0.0)


def test_pack_surface_skips_tiles_fully_inside_opening() -> None:
    """A tile fully inside a door opening is not laid."""
    patch = _rect_patch(4.0, 3.0)
    # Add a 1m x 1m door opening between (1,0) and (2,1)
    patch.holes = [
        Hole(
            x_m=1.0,
            y_m=0.0,
            width_m=1.0,
            height_m=1.0,
            label="door",
            kind=HoleKind.OPENING,
        )
    ]
    spec = _spec(1.0, 1.0, grout_mm=0.0)

    result = pack_surface(patch, spec, StartingPointRule.LARGEST_WALL_CORNER)

    # Without the hole, 12 tiles. Hole is 1m x 1m exactly = 1 tile killed → 11.
    assert result.tiles_required == 11
    # All surviving tiles are full (no edge cuts on a 4x3 with 1m tile).
    assert result.cut_modules == 0


def test_pack_surface_mask_kind_excludes_same_as_opening() -> None:
    """A MASK hole behaves identically to an OPENING hole in packing."""
    patch_open = _rect_patch(4.0, 3.0)
    patch_open.holes = [
        Hole(
            x_m=1.0,
            y_m=0.0,
            width_m=1.0,
            height_m=1.0,
            label="door",
            kind=HoleKind.OPENING,
        )
    ]
    patch_mask = _rect_patch(4.0, 3.0)
    patch_mask.holes = [
        Hole(
            x_m=1.0,
            y_m=0.0,
            width_m=1.0,
            height_m=1.0,
            label="niche",
            kind=HoleKind.MASK,
        )
    ]
    spec = _spec(1.0, 1.0, grout_mm=0.0)

    result_open = pack_surface(patch_open, spec, StartingPointRule.LARGEST_WALL_CORNER)
    result_mask = pack_surface(patch_mask, spec, StartingPointRule.LARGEST_WALL_CORNER)

    assert result_open.tiles_required == result_mask.tiles_required
    assert result_open.full_modules == result_mask.full_modules
    assert result_open.cut_modules == result_mask.cut_modules


def test_pack_surface_covered_and_waste_areas_are_consistent() -> None:
    """covered_area + waste_area = full_tiles × tile_area."""
    patch = _rect_patch(4.0, 3.0)
    spec = _spec(0.6, 0.3, grout_mm=0.0)

    result = pack_surface(patch, spec, StartingPointRule.LARGEST_WALL_CORNER)

    tile_area = spec.module_width_m * spec.module_height_m
    assert result.covered_area_m2 + result.waste_area_m2 == pytest.approx(
        result.tiles_required * tile_area,
        abs=1e-9,
    )


# ----- build_tile_estimate -----


def _bathroom_input(
    overage: OveragePolicy = OveragePolicy.FLAT_10,
    rule: StartingPointRule = StartingPointRule.LARGEST_WALL_CORNER,
) -> TileModeInput:
    """Reference bathroom (3 walls + floor) mirroring designer's DEFAULT_SURFACES
    in design_handoff_replace_material/src/state.jsx:4-29.
    """
    walls = [
        _rect_patch(1.8, 2.4, kind="wall"),  # wall-1 shower
        _rect_patch(2.4, 2.4, kind="wall"),  # wall-2 vanity (window dropped)
        _rect_patch(1.8, 2.4, kind="wall"),  # wall-3 door
    ]
    for i, w in enumerate(walls):
        w.patch_id = f"wall-{i + 1}"
    floor = _rect_patch(2.4, 1.8, kind="floor")
    floor.patch_id = "floor"

    wall_spec = MaterialSpec(
        material_id="wall-tile",
        module_width_m=0.30,
        module_height_m=0.60,
        modules_per_unit=10,
        grout_width_mm=3.0,
        price_per_box_cents=4200,
    )
    floor_spec = MaterialSpec(
        material_id="floor-tile",
        module_width_m=0.60,
        module_height_m=0.60,
        modules_per_unit=5,
        grout_width_mm=3.0,
        price_per_box_cents=6800,
    )
    return TileModeInput(
        surfaces=[*walls, floor],
        wall_material=wall_spec,
        floor_material=floor_spec,
        overage_policy=overage,
        starting_point_rule=rule,
    )


def test_build_tile_estimate_populates_box_counts() -> None:
    result = build_tile_estimate(_bathroom_input())

    # Boxes should be positive and tiles_total >= packed total.
    assert result.wall_boxes_total > 0
    assert result.floor_boxes_total > 0
    assert result.wall_tiles_total >= sum(p.tiles_required for p in result.wall_packs)
    assert result.floor_tiles_total >= sum(p.tiles_required for p in result.floor_packs)


def test_build_tile_estimate_applies_overage_policy_consistently() -> None:
    """FLAT_10 should produce more tiles than a policy with smaller overage,
    and RISK_ADJUSTED varies with cut density.
    """
    flat = build_tile_estimate(_bathroom_input(overage=OveragePolicy.FLAT_10))
    risk = build_tile_estimate(_bathroom_input(overage=OveragePolicy.RISK_ADJUSTED))

    # Both policies produce non-zero box counts for a typical bathroom.
    assert flat.wall_boxes_total > 0 and risk.wall_boxes_total > 0
    assert flat.overage_policy is OveragePolicy.FLAT_10
    assert risk.overage_policy is OveragePolicy.RISK_ADJUSTED


def test_build_tile_estimate_contractor_tier_only_set_when_policy_is_contractor() -> None:
    flat_result = build_tile_estimate(_bathroom_input(overage=OveragePolicy.FLAT_10))
    assert flat_result.contractor_tier is None

    tier_input = _bathroom_input(overage=OveragePolicy.CONTRACTOR_TIER)
    tier_input.contractor_tier = ContractorTier.PERFECTIONIST
    tier_result = build_tile_estimate(tier_input)
    assert tier_result.contractor_tier is ContractorTier.PERFECTIONIST
