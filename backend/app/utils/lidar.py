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
  "floor_area_sqm": 24.36
}
"""

from __future__ import annotations

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

    if width <= 0 or length <= 0 or height <= 0:
        raise LidarParseError(f"Room dimensions must be positive: {width}x{length}x{height}")

    walls = raw.get("walls", [])
    if not isinstance(walls, list):
        walls = []

    openings = raw.get("openings", [])
    if not isinstance(openings, list):
        openings = []

    dimensions = RoomDimensions(
        width_m=width,
        length_m=length,
        height_m=height,
        walls=walls,
        openings=openings,
    )

    logger.info(
        "lidar_parsed",
        width_m=width,
        length_m=length,
        height_m=height,
        wall_count=len(walls),
        opening_count=len(openings),
    )
    return dimensions
