"""Tests for LiDAR dimension parser — RoomPlan JSON to RoomDimensions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.contracts import RoomDimensions
from app.utils.lidar import MIN_DIMENSION_M, LidarParseError, parse_room_dimensions

FIXTURE_PATH = (
    Path(__file__).parent.parent.parent / "ios" / ".maestro" / "fixtures" / "reference_room.json"
)


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

    def test_floor_area_infinity_falls_back(self) -> None:
        """Infinity floor_area_sqm should fall back to width * length."""
        data = _valid_lidar_json()
        data["floor_area_sqm"] = float("inf")
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == pytest.approx(4.2 * 5.8)

    def test_floor_area_negative_infinity_falls_back(self) -> None:
        """Negative infinity floor_area_sqm should fall back to width * length."""
        data = _valid_lidar_json()
        data["floor_area_sqm"] = float("-inf")
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == pytest.approx(4.2 * 5.8)

    def test_malformed_furniture_entries_raises(self) -> None:
        """Non-dict items in furniture list should raise LidarParseError."""
        data = _valid_lidar_json()
        data["furniture"] = [42, "bad"]
        with pytest.raises(LidarParseError, match="Invalid scan structure"):
            parse_room_dimensions(data)

    def test_malformed_surface_entries_raises(self) -> None:
        """Non-dict items in surfaces list should raise LidarParseError."""
        data = _valid_lidar_json()
        data["surfaces"] = ["not-a-dict"]
        with pytest.raises(LidarParseError, match="Invalid scan structure"):
            parse_room_dimensions(data)

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


class TestExplicitEmptyArrays:
    """Verify explicit empty arrays produce identical output to missing arrays."""

    def test_explicit_empty_equals_missing(self) -> None:
        """Explicit empty arrays should produce same result as absent keys."""
        base = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}}
        explicit = {
            "room": {"width": 4.0, "length": 5.0, "height": 2.5},
            "walls": [],
            "openings": [],
            "furniture": [],
            "surfaces": [],
        }
        r1 = parse_room_dimensions(base)
        r2 = parse_room_dimensions(explicit)
        assert r1.walls == r2.walls == []
        assert r1.openings == r2.openings == []
        assert r1.furniture == r2.furniture == []
        assert r1.surfaces == r2.surfaces == []
        assert r1.floor_area_sqm == r2.floor_area_sqm


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


class TestUnitValidation:
    """G11: Unit validation — reject non-meter units."""

    def test_unit_meters_accepted(self) -> None:
        """Explicit 'meters' unit should be accepted."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": "meters"}}
        result = parse_room_dimensions(data)
        assert result.width_m == 4.0

    def test_unit_m_accepted(self) -> None:
        """Shorthand 'm' unit should be accepted."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": "m"}}
        result = parse_room_dimensions(data)
        assert result.width_m == 4.0

    def test_unit_absent_accepted(self) -> None:
        """No unit field should be accepted (meters assumed)."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}}
        result = parse_room_dimensions(data)
        assert result.width_m == 4.0

    def test_unit_feet_rejected(self) -> None:
        """'feet' unit should raise LidarParseError."""
        data = {"room": {"width": 14.0, "length": 19.0, "height": 9.0, "unit": "feet"}}
        with pytest.raises(LidarParseError, match="Unsupported unit.*feet"):
            parse_room_dimensions(data)

    def test_unit_inches_rejected(self) -> None:
        """'inches' unit should raise LidarParseError."""
        data = {"room": {"width": 168, "length": 228, "height": 108, "unit": "inches"}}
        with pytest.raises(LidarParseError, match="Unsupported unit.*inches"):
            parse_room_dimensions(data)

    def test_unit_case_insensitive(self) -> None:
        """Unit comparison should be case-insensitive."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": "Meters"}}
        result = parse_room_dimensions(data)
        assert result.width_m == 4.0

    def test_unit_empty_string_rejected(self) -> None:
        """Empty string unit should be rejected."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": ""}}
        with pytest.raises(LidarParseError, match="Unsupported unit"):
            parse_room_dimensions(data)

    def test_unit_non_string_type_rejected(self) -> None:
        """Non-string unit types (int, dict) should be rejected."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": 42}}
        with pytest.raises(LidarParseError, match="Unsupported unit"):
            parse_room_dimensions(data)

    def test_unit_whitespace_m_accepted(self) -> None:
        """G28: Unit 'm' with surrounding whitespace should be accepted."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": "  m  "}}
        result = parse_room_dimensions(data)
        assert result.width_m == 4.0

    def test_unit_whitespace_meters_accepted(self) -> None:
        """G28: Unit 'meters' with leading/trailing whitespace should be accepted."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": " meters "}}
        result = parse_room_dimensions(data)
        assert result.width_m == 4.0

    def test_unit_whitespace_only_rejected(self) -> None:
        """G28: Whitespace-only unit should be rejected (strips to empty)."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5, "unit": "   "}}
        with pytest.raises(LidarParseError, match="Unsupported unit"):
            parse_room_dimensions(data)


class TestUpperBounds:
    """G12: Upper bounds on dimensions — reject implausibly large values."""

    def test_reasonable_large_room_accepted(self) -> None:
        """A 20m x 30m room (large loft) should be accepted."""
        data = {"room": {"width": 20.0, "length": 30.0, "height": 4.0}}
        result = parse_room_dimensions(data)
        assert result.width_m == 20.0

    def test_at_limit_accepted(self) -> None:
        """Exactly 50m should be accepted (boundary)."""
        data = {"room": {"width": 50.0, "length": 50.0, "height": 50.0}}
        result = parse_room_dimensions(data)
        assert result.width_m == 50.0

    def test_width_over_limit_rejected(self) -> None:
        """Width exceeding 50m should raise LidarParseError."""
        data = {"room": {"width": 100.0, "length": 5.0, "height": 2.5}}
        with pytest.raises(LidarParseError, match="exceed.*50"):
            parse_room_dimensions(data)

    def test_length_over_limit_rejected(self) -> None:
        """Length exceeding 50m should raise LidarParseError."""
        data = {"room": {"width": 5.0, "length": 100.0, "height": 2.5}}
        with pytest.raises(LidarParseError, match="exceed.*50"):
            parse_room_dimensions(data)

    def test_height_over_limit_rejected(self) -> None:
        """Height exceeding 50m should raise LidarParseError."""
        data = {"room": {"width": 5.0, "length": 5.0, "height": 60.0}}
        with pytest.raises(LidarParseError, match="exceed.*50"):
            parse_room_dimensions(data)

    def test_just_over_limit_rejected(self) -> None:
        """50.1m should be rejected."""
        data = {"room": {"width": 50.1, "length": 5.0, "height": 2.5}}
        with pytest.raises(LidarParseError, match="exceed.*50"):
            parse_room_dimensions(data)


class TestFieldTypeWarnings:
    """Verify non-list fields produce warnings (not silent fallback).

    structlog writes to stdout, so we use capsys to capture warning output.
    """

    def test_walls_not_list_logs_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-list walls should log a type mismatch warning."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "walls": "invalid"}
        result = parse_room_dimensions(data)
        assert result.walls == []
        assert "lidar_field_type_mismatch" in capsys.readouterr().out

    def test_openings_not_list_logs_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-list openings should log a type mismatch warning."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "openings": 42}
        result = parse_room_dimensions(data)
        assert result.openings == []
        assert "lidar_field_type_mismatch" in capsys.readouterr().out

    def test_furniture_not_list_logs_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-list furniture should log a type mismatch warning."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "furniture": {}}
        result = parse_room_dimensions(data)
        assert result.furniture == []
        assert "lidar_field_type_mismatch" in capsys.readouterr().out

    def test_surfaces_not_list_logs_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-list surfaces should log a type mismatch warning."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "surfaces": True}
        result = parse_room_dimensions(data)
        assert result.surfaces == []
        assert "lidar_field_type_mismatch" in capsys.readouterr().out


class TestFloorAreaDiscrepancy:
    """Verify floor_area_sqm discrepancy warning.

    structlog writes to stdout, so we use capsys to capture warning output.
    """

    def test_consistent_area_no_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Consistent floor area (within 5x of computed) should not warn."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "floor_area_sqm": 18.0}
        parse_room_dimensions(data)
        assert "floor_area_discrepancy" not in capsys.readouterr().out

    def test_wildly_large_area_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Floor area >5x computed should log discrepancy warning."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "floor_area_sqm": 500.0}
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 500.0  # accepted, just warned
        assert "floor_area_discrepancy" in capsys.readouterr().out

    def test_wildly_small_area_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Floor area <0.2x computed should log discrepancy warning."""
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "floor_area_sqm": 1.0}
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 1.0  # accepted, just warned
        assert "floor_area_discrepancy" in capsys.readouterr().out


class TestLowerBounds:
    """G27: Lower bounds on dimensions — reject implausibly small values."""

    def test_small_closet_accepted(self) -> None:
        """A 0.6m x 0.6m x 2.0m closet should be accepted."""
        data = {"room": {"width": 0.6, "length": 0.6, "height": 2.0}}
        result = parse_room_dimensions(data)
        assert result.width_m == 0.6

    def test_at_minimum_accepted(self) -> None:
        """Exactly MIN_DIMENSION_M should be accepted (boundary)."""
        data = {
            "room": {
                "width": MIN_DIMENSION_M,
                "length": MIN_DIMENSION_M,
                "height": MIN_DIMENSION_M,
            }
        }
        result = parse_room_dimensions(data)
        assert result.width_m == MIN_DIMENSION_M

    def test_width_below_minimum_rejected(self) -> None:
        """Width below MIN_DIMENSION_M should raise LidarParseError."""
        data = {"room": {"width": 0.1, "length": 5.0, "height": 2.5}}
        with pytest.raises(LidarParseError, match="below.*minimum"):
            parse_room_dimensions(data)

    def test_length_below_minimum_rejected(self) -> None:
        """Length below MIN_DIMENSION_M should raise LidarParseError."""
        data = {"room": {"width": 5.0, "length": 0.1, "height": 2.5}}
        with pytest.raises(LidarParseError, match="below.*minimum"):
            parse_room_dimensions(data)

    def test_height_below_minimum_rejected(self) -> None:
        """Height below MIN_DIMENSION_M should raise LidarParseError."""
        data = {"room": {"width": 5.0, "length": 5.0, "height": 0.1}}
        with pytest.raises(LidarParseError, match="below.*minimum"):
            parse_room_dimensions(data)

    def test_just_below_minimum_rejected(self) -> None:
        """0.29m should be rejected (just under 0.3m minimum)."""
        data = {"room": {"width": 0.29, "length": 5.0, "height": 2.5}}
        with pytest.raises(LidarParseError, match="below.*minimum"):
            parse_room_dimensions(data)

    def test_tiny_positive_rejected(self) -> None:
        """Very small positive values (0.001m) should be rejected."""
        data = {"room": {"width": 0.001, "length": 0.001, "height": 0.001}}
        with pytest.raises(LidarParseError, match="below.*minimum"):
            parse_room_dimensions(data)


class TestFloorAreaDiscrepancyBoundary:
    """Boundary tests for floor_area_sqm discrepancy warning thresholds.

    The code uses strict `>` and `<` (not `>=`/`<=`), so ratios exactly at
    5.0 and 0.2 should NOT trigger warnings. This validates the boundary.
    """

    def test_exact_5x_ratio_no_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Ratio exactly 5.0 should NOT warn (code uses `ratio > 5.0`)."""
        # width=4.0, length=5.0 → computed=20.0; floor_area=100.0 → ratio=5.0
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "floor_area_sqm": 100.0}
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 100.0
        assert "floor_area_discrepancy" not in capsys.readouterr().out

    def test_exact_0_2x_ratio_no_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Ratio exactly 0.2 should NOT warn (code uses `ratio < 0.2`)."""
        # width=4.0, length=5.0 → computed=20.0; floor_area=4.0 → ratio=0.2
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "floor_area_sqm": 4.0}
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 4.0
        assert "floor_area_discrepancy" not in capsys.readouterr().out

    def test_just_above_5x_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Ratio 5.01 should warn (just above threshold)."""
        # width=4.0, length=5.0 → computed=20.0; floor_area=100.2 → ratio=5.01
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "floor_area_sqm": 100.2}
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 100.2
        assert "floor_area_discrepancy" in capsys.readouterr().out

    def test_just_below_0_2x_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Ratio 0.199 should warn (just below threshold)."""
        # width=4.0, length=5.0 → computed=20.0; floor_area=3.98 → ratio=0.199
        data = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}, "floor_area_sqm": 3.98}
        result = parse_room_dimensions(data)
        assert result.floor_area_sqm == 3.98
        assert "floor_area_discrepancy" in capsys.readouterr().out


class TestReferenceFixture:
    """Phase B3: Validate the Maestro reference fixture against the parser."""

    @pytest.mark.skipif(not FIXTURE_PATH.exists(), reason="Reference fixture not found")
    def test_reference_fixture_parses(self) -> None:
        """The reference_room.json fixture must pass the backend parser."""
        with open(FIXTURE_PATH) as f:
            data = json.load(f)
        dims = parse_room_dimensions(data)
        assert dims.width_m == pytest.approx(4.2, abs=0.1)
        assert dims.length_m == pytest.approx(5.8, abs=0.1)
        assert dims.height_m == pytest.approx(2.7, abs=0.1)
        assert dims.floor_area_sqm == pytest.approx(24.36, abs=0.1)
        assert len(dims.walls) == 4
        assert len(dims.openings) == 2
        assert len(dims.furniture) == 3
        assert len(dims.surfaces) == 1
