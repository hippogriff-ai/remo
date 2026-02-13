"""Tests for LiDAR dimension parser — RoomPlan JSON to RoomDimensions."""

from __future__ import annotations

import pytest

from app.models.contracts import RoomDimensions
from app.utils.lidar import LidarParseError, parse_room_dimensions


def _valid_lidar_json() -> dict:
    """Full valid RoomPlan JSON matching the spec schema."""
    return {
        "room": {"width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters"},
        "walls": [
            {"id": "wall_0", "width": 4.2, "height": 2.7, "orientation": 0},
            {"id": "wall_1", "width": 5.8, "height": 2.7, "orientation": 90},
            {"id": "wall_2", "width": 4.2, "height": 2.7, "orientation": 180},
            {"id": "wall_3", "width": 5.8, "height": 2.7, "orientation": 270},
        ],
        "openings": [
            {
                "type": "door",
                "wall_id": "wall_0",
                "width": 0.9,
                "height": 2.1,
                "position": {"x": 1.5},
            },
            {
                "type": "window",
                "wall_id": "wall_1",
                "width": 1.2,
                "height": 1.0,
                "position": {"x": 2.0, "y": 1.0},
            },
        ],
        "floor_area_sqm": 24.36,
    }


class TestParseRoomDimensions:
    """Tests for parse_room_dimensions."""

    def test_full_valid_input(self) -> None:
        """Complete RoomPlan JSON should parse into correct RoomDimensions."""
        result = parse_room_dimensions(_valid_lidar_json())

        assert isinstance(result, RoomDimensions)
        assert result.width_m == 4.2
        assert result.length_m == 5.8
        assert result.height_m == 2.7
        assert len(result.walls) == 4
        assert len(result.openings) == 2
        assert result.floor_area_sqm == 24.36
        assert result.furniture == []
        assert result.surfaces == []

    def test_walls_preserved(self) -> None:
        """Wall data should be passed through as-is in the walls list."""
        data = _valid_lidar_json()
        result = parse_room_dimensions(data)

        assert result.walls[0]["id"] == "wall_0"
        assert result.walls[0]["width"] == 4.2
        assert result.walls[1]["orientation"] == 90

    def test_openings_preserved(self) -> None:
        """Opening data should be passed through as-is in the openings list."""
        data = _valid_lidar_json()
        result = parse_room_dimensions(data)

        assert result.openings[0]["type"] == "door"
        assert result.openings[0]["wall_id"] == "wall_0"
        assert result.openings[1]["type"] == "window"

    def test_minimal_valid_input(self) -> None:
        """Only room dimensions required — walls and openings optional."""
        data = {"room": {"width": 3.0, "length": 4.0, "height": 2.5}}
        result = parse_room_dimensions(data)

        assert result.width_m == 3.0
        assert result.length_m == 4.0
        assert result.height_m == 2.5
        assert result.walls == []
        assert result.openings == []
        assert result.furniture == []
        assert result.surfaces == []
        assert result.floor_area_sqm == 12.0  # fallback: 3.0 * 4.0

    def test_extra_fields_ignored(self) -> None:
        """Unknown top-level fields should not cause errors."""
        data = _valid_lidar_json()
        data["metadata"] = {"scanner_version": "2.0"}
        result = parse_room_dimensions(data)

        assert result.width_m == 4.2

    def test_integer_dimensions_coerced(self) -> None:
        """Integer values should be coerced to float."""
        data = {"room": {"width": 4, "length": 5, "height": 3}}
        result = parse_room_dimensions(data)

        assert result.width_m == 4.0
        assert isinstance(result.width_m, float)

    def test_string_dimensions_coerced(self) -> None:
        """Numeric strings should be coerced to float."""
        data = {"room": {"width": "4.2", "length": "5.8", "height": "2.7"}}
        result = parse_room_dimensions(data)

        assert result.width_m == 4.2


class TestFurnitureSurfacesFloorArea:
    """Tests for furniture, surfaces, and floor_area_sqm parsing."""

    def test_furniture_parsed(self) -> None:
        """Furniture array should be passed through to RoomDimensions."""
        data = _valid_lidar_json()
        data["furniture"] = [
            {"type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8},
            {"type": "table", "width": 1.2, "depth": 0.6, "height": 0.75},
        ]
        result = parse_room_dimensions(data)
        assert len(result.furniture) == 2
        assert result.furniture[0]["type"] == "sofa"

    def test_surfaces_parsed(self) -> None:
        """Surfaces array should be passed through to RoomDimensions."""
        data = _valid_lidar_json()
        data["surfaces"] = [{"type": "floor", "material": "hardwood"}]
        result = parse_room_dimensions(data)
        assert len(result.surfaces) == 1
        assert result.surfaces[0]["material"] == "hardwood"

    def test_floor_area_from_roomplan(self) -> None:
        """floor_area_sqm from RoomPlan should be used when valid."""
        data = _valid_lidar_json()
        data["floor_area_sqm"] = 24.36
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 24.36

    def test_floor_area_fallback_to_computed(self) -> None:
        """Without floor_area_sqm, should fall back to width * length."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}}
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 20.0

    def test_floor_area_invalid_nan_falls_back(self) -> None:
        """NaN floor_area_sqm should fall back to width * length."""
        data = _valid_lidar_json()
        data["floor_area_sqm"] = float("nan")
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == pytest.approx(4.2 * 5.8)

    def test_floor_area_negative_falls_back(self) -> None:
        """Negative floor_area_sqm should fall back to width * length."""
        data = _valid_lidar_json()
        data["floor_area_sqm"] = -10.0
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == pytest.approx(4.2 * 5.8)

    def test_floor_area_zero_falls_back(self) -> None:
        """Zero floor_area_sqm should fall back to width * length."""
        data = _valid_lidar_json()
        data["floor_area_sqm"] = 0
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == pytest.approx(4.2 * 5.8)

    def test_floor_area_non_numeric_falls_back(self) -> None:
        """Non-numeric floor_area_sqm should fall back to width * length."""
        data = _valid_lidar_json()
        data["floor_area_sqm"] = "big"
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == pytest.approx(4.2 * 5.8)

    def test_furniture_not_list_treated_as_empty(self) -> None:
        """Non-list furniture should be treated as empty."""
        data = _valid_lidar_json()
        data["furniture"] = "invalid"
        result = parse_room_dimensions(data)
        assert result.furniture == []

    def test_surfaces_not_list_treated_as_empty(self) -> None:
        """Non-list surfaces should be treated as empty."""
        data = _valid_lidar_json()
        data["surfaces"] = 42
        result = parse_room_dimensions(data)
        assert result.surfaces == []

    def test_full_valid_input_includes_new_fields(self) -> None:
        """Full input with all new fields should parse correctly."""
        data = _valid_lidar_json()
        data["furniture"] = [{"type": "chair"}]
        data["surfaces"] = [{"type": "floor", "material": "tile"}]
        data["floor_area_sqm"] = 25.0
        result = parse_room_dimensions(data)
        assert result.furniture == [{"type": "chair"}]
        assert result.surfaces == [{"type": "floor", "material": "tile"}]
        assert result.floor_area_sqm == 25.0

    def test_minimal_input_defaults_new_fields(self) -> None:
        """Minimal input should default new fields properly."""
        data = {"room": {"width": 3.0, "length": 4.0, "height": 2.5}}
        result = parse_room_dimensions(data)
        assert result.furniture == []
        assert result.surfaces == []
        assert result.floor_area_sqm == 12.0  # 3.0 * 4.0


class TestParseRoomDimensionsErrors:
    """Tests for error cases in parse_room_dimensions."""

    def test_missing_room_key(self) -> None:
        """Missing 'room' key should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid 'room'"):
            parse_room_dimensions({"walls": []})

    def test_room_is_not_dict(self) -> None:
        """Non-dict 'room' value should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid 'room'"):
            parse_room_dimensions({"room": "invalid"})

    def test_room_is_none(self) -> None:
        """None 'room' value should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid 'room'"):
            parse_room_dimensions({"room": None})

    def test_missing_width(self) -> None:
        """Missing width should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid room dimension"):
            parse_room_dimensions({"room": {"length": 5.0, "height": 2.5}})

    def test_missing_length(self) -> None:
        """Missing length should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid room dimension"):
            parse_room_dimensions({"room": {"width": 4.0, "height": 2.5}})

    def test_missing_height(self) -> None:
        """Missing height should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid room dimension"):
            parse_room_dimensions({"room": {"width": 4.0, "length": 5.0}})

    def test_non_numeric_width(self) -> None:
        """Non-numeric width should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid room dimension"):
            parse_room_dimensions({"room": {"width": "abc", "length": 5.0, "height": 2.5}})

    def test_zero_dimension(self) -> None:
        """Zero dimension should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="must be positive"):
            parse_room_dimensions({"room": {"width": 0, "length": 5.0, "height": 2.5}})

    def test_negative_dimension(self) -> None:
        """Negative dimension should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="must be positive"):
            parse_room_dimensions({"room": {"width": -4.0, "length": 5.0, "height": 2.5}})

    def test_nan_dimension(self) -> None:
        """NaN dimension should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="must be finite"):
            parse_room_dimensions({"room": {"width": float("nan"), "length": 5.0, "height": 2.5}})

    def test_infinity_dimension(self) -> None:
        """Infinity dimension should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="must be finite"):
            parse_room_dimensions({"room": {"width": 4.0, "length": float("inf"), "height": 2.5}})

    def test_negative_infinity_dimension(self) -> None:
        """Negative infinity dimension should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="must be finite"):
            parse_room_dimensions({"room": {"width": 4.0, "length": 5.0, "height": float("-inf")}})

    def test_walls_not_list_treated_as_empty(self) -> None:
        """Non-list walls should be treated as empty (graceful degradation)."""
        data = {
            "room": {"width": 4.0, "length": 5.0, "height": 2.5},
            "walls": "invalid",
        }
        result = parse_room_dimensions(data)
        assert result.walls == []

    def test_malformed_wall_entries_raises(self) -> None:
        """Non-dict items in walls list should raise LidarParseError (not 500)."""
        data = {
            "room": {"width": 4.0, "length": 5.0, "height": 2.5},
            "walls": [42, "bad"],
        }
        with pytest.raises(LidarParseError, match="Invalid scan structure"):
            parse_room_dimensions(data)

    def test_malformed_opening_entries_raises(self) -> None:
        """Non-dict items in openings list should raise LidarParseError (not 500)."""
        data = {
            "room": {"width": 4.0, "length": 5.0, "height": 2.5},
            "openings": [42],
        }
        with pytest.raises(LidarParseError, match="Invalid scan structure"):
            parse_room_dimensions(data)

    def test_openings_not_list_treated_as_empty(self) -> None:
        """Non-list openings should be treated as empty (graceful degradation)."""
        data = {
            "room": {"width": 4.0, "length": 5.0, "height": 2.5},
            "openings": 42,
        }
        result = parse_room_dimensions(data)
        assert result.openings == []

    def test_empty_dict_raises(self) -> None:
        """Empty dict should raise LidarParseError."""
        with pytest.raises(LidarParseError, match="Missing or invalid 'room'"):
            parse_room_dimensions({})
