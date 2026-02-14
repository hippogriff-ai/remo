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

from app.models.contracts import RoomDimensions

logger = structlog.get_logger()


class LidarParseError(Exception):
    """Raised when RoomPlan JSON cannot be parsed into RoomDimensions."""


def parse_room_dimensions(raw: dict) -> RoomDimensions:
    """Parse RoomPlan JSON into a RoomDimensions model.

    Raises LidarParseError if required fields are missing or invalid.
    """
    room = raw.get("room")
    if not isinstance(room, dict):
        raise LidarParseError("Missing or invalid 'room' object in LiDAR data")

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

    walls = raw.get("walls", [])
    if not isinstance(walls, list):
        walls = []

    openings = raw.get("openings", [])
    if not isinstance(openings, list):
        openings = []

    furniture = raw.get("furniture", [])
    if not isinstance(furniture, list):
        furniture = []

    surfaces = raw.get("surfaces", [])
    if not isinstance(surfaces, list):
        surfaces = []

    # Prefer RoomPlan's floor_area_sqm if valid; fall back to width * length
    raw_area = raw.get("floor_area_sqm")
    floor_area_sqm: float | None = None
    if raw_area is not None:
        try:
            area = float(raw_area)
            if math.isfinite(area) and area > 0:
                floor_area_sqm = area
        except (TypeError, ValueError):
            pass
    if floor_area_sqm is None:
        floor_area_sqm = width * length

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
