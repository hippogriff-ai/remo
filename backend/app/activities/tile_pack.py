"""Tile packing — pure deterministic functions, no AI, no I/O.

Given a SurfacePatch + MaterialSpec + policies, produces:
  - where tile #1 starts on the surface (choose_origin)
  - a tile layout with every rect + cut flag (pack_surface)
  - contractor overage applied to a packed tile count (apply_overage)
  - a full wall+floor TileModeEstimate with box counts (build_tile_estimate)

This module is the canonical math for tile mode. iOS ports the same algorithm
to Swift for live-toggle re-render (<16ms target on iPhone 13+); a parity
test compares Python and Swift outputs against a shared fixture suite (see PR 4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.contracts import (
    ContractorTier,
    Hole,
    MaterialSpec,
    OveragePolicy,
    PackResult,
    StartingPointRule,
    SurfacePatch,
    TileModeEstimate,
    TileModeInput,
    TileRect,
    Vec2,
)

# Edge-cut below this dimension (in meters) is considered an "ugly sliver"
# that will crack during install. Used by the MINIMIZE_CUT_COUNT scorer.
_SLIVER_THRESHOLD_M = 0.05  # 5 cm

_TIER_OVERAGE = {
    ContractorTier.APPRENTICE: 0.15,
    ContractorTier.PRO: 0.12,
    ContractorTier.PERFECTIONIST: 0.18,
}

# Trim IEEE-754 drift before ceil() so e.g. 100 * 1.10 rounds to 110, not 111.
_CEIL_EPS = 1e-9


@dataclass(frozen=True)
class _BBox:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y


# ----- public API -----


def choose_origin(patch: SurfacePatch, spec: MaterialSpec, rule: StartingPointRule) -> Vec2:
    """Return the (x, y) in surface-local meters where tile #1 should start."""
    bbox = _polygon_bbox(patch.polygon)
    if rule is StartingPointRule.CENTERED_FOCAL_WALL:
        # Center one tile on the surface's centroid so slivers are symmetric.
        cx = (bbox.min_x + bbox.max_x) / 2.0
        cy = (bbox.min_y + bbox.max_y) / 2.0
        return Vec2(
            x=cx - spec.module_width_m / 2.0,
            y=cy - spec.module_height_m / 2.0,
        )
    if rule is StartingPointRule.LARGEST_WALL_CORNER:
        # Start from the bounding-box corner — two sides get full tiles,
        # two sides get cuts. Fewer total cuts than centered.
        return Vec2(x=bbox.min_x, y=bbox.min_y)
    if rule is StartingPointRule.MINIMIZE_CUT_COUNT:
        return _choose_origin_minimize_cuts(patch, spec)
    raise ValueError(f"unknown StartingPointRule: {rule!r}")


def apply_overage(
    tiles_needed: int,
    policy: OveragePolicy,
    *,
    cut_count: int,
    tier: ContractorTier = ContractorTier.PRO,
) -> int:
    """Inflate packed tile count by the contractor overage policy.

    Formulas match the designer's canonical math in
    design_handoff_replace_material/src/state.jsx:57-74.
    """
    if policy is OveragePolicy.FLAT_10:
        return math.ceil(tiles_needed * 1.10 - _CEIL_EPS)
    if policy is OveragePolicy.CONTRACTOR_TIER:
        return math.ceil(tiles_needed * (1.0 + _TIER_OVERAGE[tier]) - _CEIL_EPS)
    if policy is OveragePolicy.RISK_ADJUSTED:
        # 8% + (cut_count / tile_count) * 12%, capped at 20% total.
        tile_count = max(tiles_needed, 1)
        cut_ratio = min(1.0, cut_count / tile_count)
        pct = 0.08 + cut_ratio * 0.12
        return math.ceil(tiles_needed * (1.0 + pct) - _CEIL_EPS)
    raise ValueError(f"unknown OveragePolicy: {policy!r}")


# ----- MINIMIZE_CUT_COUNT origin search -----


def _choose_origin_minimize_cuts(patch: SurfacePatch, spec: MaterialSpec) -> Vec2:
    """Pick an x-origin that minimizes edge-cut waste along the surface width.

    Ported from design_handoff_replace_material/src/cutsheet.jsx:25-35.
    Bug fix vs. the JS reference: that code's `score > best.score * 0.001`
    guard evaluates false on the first iteration (best.score starts at
    Infinity), so the JS path silently falls back to x0 = 0 every time. We
    implement a correct minimum-finder here.

    Sweeps x0 across one tile-width in 21 samples; scores each candidate by
    how close its left-edge and right-edge cuts sit to a tile boundary.
    Y-origin is fixed at bbox.min_y (stagger patterns not yet supported).
    """
    bbox = _polygon_bbox(patch.polygon)
    width = bbox.max_x - bbox.min_x
    tile_w = spec.module_width_m
    grout_m = spec.grout_width_mm / 1000.0
    step_x = tile_w + grout_m

    best_x_rel = 0.0
    best_score = math.inf
    for i in range(21):
        ox_rel = -tile_w + (i / 20) * tile_w
        left_cut = ((ox_rel % step_x) + step_x) % step_x
        right_edge = (width - ox_rel) % step_x
        score = min(left_cut, step_x - left_cut) + min(right_edge, step_x - right_edge)
        if score < best_score:
            best_score = score
            best_x_rel = ox_rel
    return Vec2(x=bbox.min_x + best_x_rel, y=bbox.min_y)


# ----- geometry + scoring helpers (available to the user's impl above) -----


def _polygon_bbox(polygon: list[Vec2]) -> _BBox:
    xs = [p.x for p in polygon]
    ys = [p.y for p in polygon]
    return _BBox(min_x=min(xs), min_y=min(ys), max_x=max(xs), max_y=max(ys))


# ----- packer -----


def pack_surface(
    patch: SurfacePatch,
    spec: MaterialSpec,
    rule: StartingPointRule,
) -> PackResult:
    """Lay tiles on a surface and return a deterministic PackResult.

    Ported from design_handoff_replace_material/src/cutsheet.jsx:9-80.
    Differences from the JS reference:
      - Meters instead of cm (SurfacePatch is meter-native).
      - Uses SurfacePatch.holes (typed) instead of raw {x,y,w,h} dicts.
      - Produces TileRect objects typed against our Pydantic contract.

    Holes (openings + masks) are handled identically: tiles fully inside any
    hole are skipped entirely; tiles overlapping a hole edge are still laid
    (they become cut tiles at install time). This matches the JS reference.
    """
    bbox = _polygon_bbox(patch.polygon)
    grout_m = spec.grout_width_mm / 1000.0
    step_x = spec.module_width_m + grout_m
    step_y = spec.module_height_m + grout_m

    origin = choose_origin(patch, spec, rule)

    # Tiles live on a grid anchored at `origin` with stride (step_x, step_y).
    # Tile i occupies [origin.x + i*step_x, origin.x + i*step_x + module_width_m].
    # We iterate every i whose tile intersects the bbox, which means backing
    # up to i_lo_x — the smallest i with tile right edge > bbox.min_x.
    #
    # This matters for CENTERED_FOCAL_WALL (origin > bbox.min) — without the
    # back-iteration the left/top strip of the surface gets no tiles and the
    # count is silently low. (Caught by Codex review on PR 21.)
    i_lo_x = math.floor((bbox.min_x - spec.module_width_m - origin.x) / step_x) + 1
    j_lo_y = math.floor((bbox.min_y - spec.module_height_m - origin.y) / step_y) + 1

    rects: list[TileRect] = []
    cut_threshold_eps = 1e-4  # matches JS "tile - 0.1" at cm scale

    j = j_lo_y
    row = 0
    while True:
        y = origin.y + j * step_y
        if y >= bbox.max_y - 1e-9:
            break
        i = i_lo_x
        col = 0
        while True:
            x = origin.x + i * step_x
            if x >= bbox.max_x - 1e-9:
                break
            left = max(x, bbox.min_x)
            top = max(y, bbox.min_y)
            right = min(x + spec.module_width_m, bbox.max_x)
            bottom = min(y + spec.module_height_m, bbox.max_y)
            if right <= left or bottom <= top:
                i += 1
                col += 1
                continue

            clipped_w = right - left
            clipped_h = bottom - top
            is_cut = (
                clipped_w + cut_threshold_eps < spec.module_width_m
                or clipped_h + cut_threshold_eps < spec.module_height_m
            )

            if not _fully_inside_any_hole(left, top, right, bottom, patch.holes):
                rects.append(
                    TileRect(
                        x_m=left,
                        y_m=top,
                        width_m=clipped_w,
                        height_m=clipped_h,
                        row=row,
                        col=col,
                        is_cut=is_cut,
                    )
                )
            i += 1
            col += 1
        j += 1
        row += 1

    full_modules = sum(1 for r in rects if not r.is_cut)
    cut_modules = sum(1 for r in rects if r.is_cut)
    covered_area = sum(r.width_m * r.height_m for r in rects)
    full_tile_area = spec.module_width_m * spec.module_height_m
    waste_area = sum(full_tile_area - r.width_m * r.height_m for r in rects)

    return PackResult(
        surface_id=patch.patch_id,
        full_modules=full_modules,
        cut_modules=cut_modules,
        tiles_required=full_modules + cut_modules,
        rects=rects,
        covered_area_m2=covered_area,
        waste_area_m2=waste_area,
        start_x_m=origin.x,
        start_y_m=origin.y,
    )


def _fully_inside_any_hole(
    left: float, top: float, right: float, bottom: float, holes: list[Hole]
) -> bool:
    """True if the rectangle [left,right] × [top,bottom] is fully inside
    any hole. Matches the JS reference's `killed` check at cutsheet.jsx:56-60.
    """
    for h in holes:
        if (
            left >= h.x_m
            and right <= h.x_m + h.width_m
            and top >= h.y_m
            and bottom <= h.y_m + h.height_m
        ):
            return True
    return False


# ----- estimate orchestrator -----


def build_tile_estimate(tile_input: TileModeInput) -> TileModeEstimate:
    """Run pack_surface per surface, sum by wall/floor, apply overage + box math.

    The canonical math iOS will mirror client-side; network calls return the
    full estimate only once policies are confirmed.

    Matches the reference logic in
    design_handoff_replace_material/src/state.jsx:83-137.
    """
    wall_packs: list[PackResult] = []
    floor_packs: list[PackResult] = []

    for surface in tile_input.surfaces:
        if surface.kind == "floor":
            floor_packs.append(
                pack_surface(surface, tile_input.floor_material, tile_input.starting_point_rule)
            )
        else:
            # walls + ceilings (ceilings treated as walls for tile accounting)
            wall_packs.append(
                pack_surface(surface, tile_input.wall_material, tile_input.starting_point_rule)
            )

    wall_tiles_needed = sum(p.tiles_required for p in wall_packs)
    wall_cut_count = sum(p.cut_modules for p in wall_packs)
    floor_tiles_needed = sum(p.tiles_required for p in floor_packs)
    floor_cut_count = sum(p.cut_modules for p in floor_packs)

    wall_tiles_total = apply_overage(
        wall_tiles_needed,
        tile_input.overage_policy,
        cut_count=wall_cut_count,
        tier=tile_input.contractor_tier,
    )
    floor_tiles_total = apply_overage(
        floor_tiles_needed,
        tile_input.overage_policy,
        cut_count=floor_cut_count,
        tier=tile_input.contractor_tier,
    )

    wall_boxes_total = math.ceil(wall_tiles_total / tile_input.wall_material.modules_per_unit)
    floor_boxes_total = math.ceil(floor_tiles_total / tile_input.floor_material.modules_per_unit)

    return TileModeEstimate(
        wall_material=tile_input.wall_material,
        floor_material=tile_input.floor_material,
        wall_packs=wall_packs,
        floor_packs=floor_packs,
        wall_tiles_total=wall_tiles_total,
        floor_tiles_total=floor_tiles_total,
        wall_boxes_total=wall_boxes_total,
        floor_boxes_total=floor_boxes_total,
        overage_policy=tile_input.overage_policy,
        contractor_tier=tile_input.contractor_tier
        if tile_input.overage_policy is OveragePolicy.CONTRACTOR_TIER
        else None,
        starting_point_rule=tile_input.starting_point_rule,
    )


# ----- scoring helper for MINIMIZE_CUT_COUNT (kept for future refinement) -----


def _count_cuts_and_slivers(
    patch: SurfacePatch, spec: MaterialSpec, origin: Vec2
) -> tuple[int, int]:
    """Lay a regular grid starting at `origin` over the polygon's bbox.

    Returns (cut_count, sliver_count):
      - cut_count: number of grid cells clipped by the bbox edge
      - sliver_count: subset of cut cells whose clipped dimension falls below
        the ugly-sliver threshold (5 cm)

    This is a fast scorer for comparing origin candidates — NOT a full packer.
    Holes are ignored (conservative; actual packing will handle them).
    """
    bbox = _polygon_bbox(patch.polygon)
    grout_m = spec.grout_width_mm / 1000.0
    w = spec.module_width_m + grout_m
    h = spec.module_height_m + grout_m
    eps = 1e-9

    # Integer cell indices relative to origin. Tolerate origin < bbox.min_*.
    i_start = math.floor((bbox.min_x - origin.x) / w + eps)
    i_end = math.ceil((bbox.max_x - origin.x) / w - eps)
    j_start = math.floor((bbox.min_y - origin.y) / h + eps)
    j_end = math.ceil((bbox.max_y - origin.y) / h - eps)

    cut_count = 0
    sliver_count = 0
    for j in range(j_start, j_end):
        for i in range(i_start, i_end):
            x = origin.x + i * w
            y = origin.y + j * h
            cx0 = max(x, bbox.min_x)
            cy0 = max(y, bbox.min_y)
            cx1 = min(x + w, bbox.max_x)
            cy1 = min(y + h, bbox.max_y)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            clipped_w = cx1 - cx0
            clipped_h = cy1 - cy0
            is_partial = clipped_w + eps < w or clipped_h + eps < h
            if is_partial:
                cut_count += 1
                if min(clipped_w, clipped_h) < _SLIVER_THRESHOLD_M:
                    sliver_count += 1
    return cut_count, sliver_count
