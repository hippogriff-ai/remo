"""LiDAR dimension parser — converts RoomPlan JSON into RoomDimensions model.

T1 iOS extracts dimensions from Apple's RoomPlan API on-device and sends
structured JSON. This parser validates and converts that JSON into the
RoomDimensions Pydantic model used by the generation and shopping pipelines.

Expected input schema (from T1 iOS):
{
  "room": { "width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters" },
  "walls": [
    { "id": "wall_0", "width": 4.2, "height": 2.7, "orientation": 0 }
  ],
  "openings": [
    { "type": "door", "wall_id": "wall_0", "width": 0.9, "height": 2.1,
      "position": { "x": 1.5 } }
  ],
  "furniture": [
    { "type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8 }
  ],
  "surfaces": [
    { "type": "floor", "material": "hardwood" }
  ],
  "floor_area_sqm": 24.36
}
"""

from __future__ import annotations

import math

import structlog

from app.models.contracts import Hole, HoleKind, RoomDimensions, SurfacePatch, Vec2

logger = structlog.get_logger()


class LidarParseError(Exception):
    """Raised when RoomPlan JSON cannot be parsed into RoomDimensions."""


# RoomPlan always outputs meters. Accept "meters"/"m" or absent unit field.
_VALID_UNITS = {"meters", "m"}

# Reasonable lower bound for a single room dimension in meters.
# 0.3m (30cm) is smaller than any plausible room (a small closet is ~0.6m).
# Prevents degenerate dimensions from reaching generation/shopping.
MIN_DIMENSION_M = 0.3

# Reasonable upper bounds for a single room dimension in meters.
# 50m covers the largest plausible residential/commercial room (e.g., warehouse loft).
MAX_DIMENSION_M = 50.0


def parse_room_dimensions(raw: dict) -> RoomDimensions:
    """Parse RoomPlan JSON into a RoomDimensions model.

    Raises LidarParseError if required fields are missing or invalid.
    """
    room = raw.get("room")
    if not isinstance(room, dict):
        raise LidarParseError("Missing or invalid 'room' object in LiDAR data")

    # G11: Validate unit field if present — reject non-meter units
    # G28: Strip whitespace before validation (tolerant of "  m  " from RoomPlan)
    unit = room.get("unit")
    if unit is not None and str(unit).strip().lower() not in _VALID_UNITS:
        raise LidarParseError(f"Unsupported unit '{unit}': only meters are accepted")

    try:
        width = float(room["width"])
        length = float(room["length"])
        height = float(room["height"])
    except (KeyError, TypeError, ValueError) as e:
        raise LidarParseError(f"Missing or invalid room dimension: {e}") from e

    if not (math.isfinite(width) and math.isfinite(length) and math.isfinite(height)):
        raise LidarParseError(f"Room dimensions must be finite: {width}x{length}x{height}")

    if width <= 0 or length <= 0 or height <= 0:
        raise LidarParseError(f"Room dimensions must be positive: {width}x{length}x{height}")

    # G27: Reject implausibly small dimensions
    if width < MIN_DIMENSION_M or length < MIN_DIMENSION_M or height < MIN_DIMENSION_M:
        raise LidarParseError(
            f"Room dimensions below {MIN_DIMENSION_M}m minimum: {width}x{length}x{height}"
        )

    # G12: Reject implausibly large dimensions
    if width > MAX_DIMENSION_M or length > MAX_DIMENSION_M or height > MAX_DIMENSION_M:
        raise LidarParseError(
            f"Room dimensions exceed {MAX_DIMENSION_M}m limit: {width}x{length}x{height}"
        )

    walls = raw.get("walls", [])
    if not isinstance(walls, list):
        logger.warning(
            "lidar_field_type_mismatch", field="walls", received_type=type(walls).__name__
        )
        walls = []

    openings = raw.get("openings", [])
    if not isinstance(openings, list):
        logger.warning(
            "lidar_field_type_mismatch", field="openings", received_type=type(openings).__name__
        )
        openings = []

    furniture = raw.get("furniture", [])
    if not isinstance(furniture, list):
        logger.warning(
            "lidar_field_type_mismatch", field="furniture", received_type=type(furniture).__name__
        )
        furniture = []

    surfaces = raw.get("surfaces", [])
    if not isinstance(surfaces, list):
        logger.warning(
            "lidar_field_type_mismatch", field="surfaces", received_type=type(surfaces).__name__
        )
        surfaces = []

    # Prefer RoomPlan's floor_area_sqm if valid; fall back to width * length
    raw_area = raw.get("floor_area_sqm")
    floor_area_sqm: float | None = None
    if raw_area is not None:
        try:
            area = float(raw_area)
            if math.isfinite(area) and area > 0:
                floor_area_sqm = area
            else:
                logger.warning(
                    "floor_area_rejected", raw_value=raw_area, reason="not finite or positive"
                )
        except (TypeError, ValueError):
            logger.warning(
                "floor_area_rejected", raw_value=str(raw_area)[:50], reason="not numeric"
            )
    if floor_area_sqm is None:
        floor_area_sqm = width * length
    else:
        # Sanity check: flag extreme discrepancy between reported and computed area.
        # Irregular rooms (L-shaped, etc.) may differ moderately, but >5x or <0.2x
        # signals data corruption or unit confusion (e.g., sq ft vs sq m).
        computed = width * length
        if computed > 0:
            ratio = floor_area_sqm / computed
            if ratio > 5.0 or ratio < 0.2:
                logger.warning(
                    "floor_area_discrepancy",
                    reported=floor_area_sqm,
                    computed=round(computed, 2),
                    ratio=round(ratio, 2),
                )

    try:
        dimensions = RoomDimensions(
            width_m=width,
            length_m=length,
            height_m=height,
            walls=walls,
            openings=openings,
            furniture=furniture,
            surfaces=surfaces,
            floor_area_sqm=floor_area_sqm,
        )
    except (ValueError, TypeError) as e:
        raise LidarParseError(f"Invalid scan structure: {e}") from e

    logger.info(
        "lidar_parsed",
        width_m=width,
        length_m=length,
        height_m=height,
        wall_count=len(walls),
        opening_count=len(openings),
        furniture_count=len(furniture),
        surface_count=len(surfaces),
        floor_area_sqm=floor_area_sqm,
    )
    return dimensions


def patches_from_room_dimensions(dims: RoomDimensions) -> list[SurfacePatch]:
    """Project RoomDimensions → list[SurfacePatch] for tile mode.

    Produces one floor patch (width × length) + one wall patch per entry in
    ``dims.walls`` (falling back to inferring 4 walls from the room bbox when
    ``dims.walls`` is empty).

    Openings attached to a wall (``opening["wall_id"] == wall.id``) become
    ``Hole(kind=OPENING)``. User-drawn masks are added later via the API
    signal ``set_surfaces`` — not here.

    All coordinates are in meters, surface-local. The patch's ``axis`` is
    x-unit (tiles run left-to-right by default); ``origin`` is (0, 0) — the
    packer chooses the real origin via ``choose_origin`` at pack time.
    """
    patches: list[SurfacePatch] = []

    # --- Floor ---
    floor_polygon = [
        Vec2(x=0.0, y=0.0),
        Vec2(x=dims.width_m, y=0.0),
        Vec2(x=dims.width_m, y=dims.length_m),
        Vec2(x=0.0, y=dims.length_m),
    ]
    patches.append(
        SurfacePatch(
            patch_id="floor",
            kind="floor",
            polygon=floor_polygon,
            holes=[],
            axis=Vec2(x=1.0, y=0.0),
            origin=Vec2(x=0.0, y=0.0),
            normal_x=0.0,
            normal_y=0.0,
            normal_z=1.0,
        )
    )

    # --- Walls: use dims.walls when provided; else infer 4 from the bbox. ---
    walls = dims.walls if dims.walls else _infer_walls_from_bbox(dims)
    for i, wall in enumerate(walls):
        try:
            w_width = float(wall.get("width", 0.0))
            w_height = float(wall.get("height", dims.height_m))
        except (TypeError, ValueError):
            continue
        if w_width <= 0 or w_height <= 0:
            continue
        wall_id = wall.get("id") or f"wall_{i}"

        holes: list[Hole] = []
        for opening in dims.openings:
            if opening.get("wall_id") != wall_id:
                continue
            position = opening.get("position") or {}
            opening_type = str(opening.get("type", "opening")).lower()
            try:
                hx = float(position.get("x", 0.0))
                hw = float(opening.get("width", 0.0))
                hh = float(opening.get("height", 0.0))
            except (TypeError, ValueError):
                continue
            if hw <= 0 or hh <= 0:
                continue
            hy = _opening_y_meters(position, opening_type, wall_height=w_height, opening_height=hh)
            # Clamp so the hole stays within the wall polygon — a malformed
            # RoomPlan frame shouldn't produce a SurfacePatch with a hole
            # hanging off the top.
            if hy + hh > w_height:
                hy = max(0.0, w_height - hh)
            holes.append(
                Hole(
                    x_m=hx,
                    y_m=hy,
                    width_m=hw,
                    height_m=hh,
                    label=opening_type,
                    kind=HoleKind.OPENING,
                )
            )

        patches.append(
            SurfacePatch(
                patch_id=str(wall_id),
                kind="wall",
                polygon=[
                    Vec2(x=0.0, y=0.0),
                    Vec2(x=w_width, y=0.0),
                    Vec2(x=w_width, y=w_height),
                    Vec2(x=0.0, y=w_height),
                ],
                holes=holes,
                axis=Vec2(x=1.0, y=0.0),
                origin=Vec2(x=0.0, y=0.0),
                normal_x=0.0,
                normal_y=1.0,
                normal_z=0.0,
            )
        )

    return patches


def _infer_walls_from_bbox(dims: RoomDimensions) -> list[dict]:
    """Fallback: infer 4 rectangular walls from room bbox when dims.walls is empty."""
    return [
        {"id": "wall_0", "width": dims.width_m, "height": dims.height_m},
        {"id": "wall_1", "width": dims.length_m, "height": dims.height_m},
        {"id": "wall_2", "width": dims.width_m, "height": dims.height_m},
        {"id": "wall_3", "width": dims.length_m, "height": dims.height_m},
    ]


# Typical sill height — used when RoomPlan doesn't include position.y for a
# window. 0.9m matches US residential code minimums (42" rail for openable
# windows on upper floors, but sill itself commonly lands 30–36").
_DEFAULT_WINDOW_SILL_M = 0.9


def _opening_y_meters(
    position: dict,
    opening_type: str,
    *,
    wall_height: float,
    opening_height: float,
) -> float:
    """Resolve the y-offset of an opening on its wall (surface-local meters).

    Honors an explicit ``position.y`` when the client provides one. Falls back
    to 0.0 for doors / generic openings and to a typical sill height for
    windows, clamped to keep the opening on the wall.
    """
    try:
        y_raw = position.get("y")
    except AttributeError:
        y_raw = None
    if y_raw is not None:
        try:
            return max(0.0, float(y_raw))
        except (TypeError, ValueError):
            pass
    if opening_type == "window":
        return min(_DEFAULT_WINDOW_SILL_M, max(0.0, wall_height - opening_height))
    return 0.0
