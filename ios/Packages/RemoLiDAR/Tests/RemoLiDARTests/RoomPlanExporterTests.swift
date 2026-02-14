import XCTest
@testable import RemoLiDAR

final class RoomPlanExporterTests: XCTestCase {

    // MARK: - round2 tests

    /// Verifies basic rounding to 2 decimal places.
    func testRound2BasicValues() {
        XCTAssertEqual(RoomPlanExporter.round2(4.256), 4.26)
        XCTAssertEqual(RoomPlanExporter.round2(4.254), 4.25)
        XCTAssertEqual(RoomPlanExporter.round2(4.255), 4.26) // banker's rounding: .5 rounds up
        XCTAssertEqual(RoomPlanExporter.round2(0.0), 0.0)
        XCTAssertEqual(RoomPlanExporter.round2(1.0), 1.0)
    }

    /// Verifies rounding of negative values (wall dimensions use abs, but the function handles negatives).
    func testRound2NegativeValues() {
        XCTAssertEqual(RoomPlanExporter.round2(-3.456), -3.46)
        XCTAssertEqual(RoomPlanExporter.round2(-0.001), 0.0)
    }

    /// Verifies rounding preserves values that already have ≤2 decimal places.
    func testRound2AlreadyRounded() {
        XCTAssertEqual(RoomPlanExporter.round2(2.7), 2.7)
        XCTAssertEqual(RoomPlanExporter.round2(2.70), 2.7)
        XCTAssertEqual(RoomPlanExporter.round2(10.0), 10.0)
    }

    /// Verifies rounding of small values near zero.
    /// Float(0.005) is stored as ~0.00499... so it rounds down — this is expected Float behavior.
    func testRound2SmallValues() {
        XCTAssertEqual(RoomPlanExporter.round2(0.004), 0.0)
        XCTAssertEqual(RoomPlanExporter.round2(0.005), 0.0) // Float(0.005) < 0.005 exactly
        XCTAssertEqual(RoomPlanExporter.round2(0.006), 0.01)
        XCTAssertEqual(RoomPlanExporter.round2(0.01), 0.01)
    }

    // MARK: - computeRoomDimensions tests

    /// Empty walls array returns zero dimensions.
    func testComputeRoomDimensionsEmpty() {
        let (w, l, h) = RoomPlanExporter.computeRoomDimensions([])
        XCTAssertEqual(w, 0)
        XCTAssertEqual(l, 0)
        XCTAssertEqual(h, 0)
    }

    /// Simple rectangular room: 4 walls forming a 4m × 6m room, 2.7m tall.
    /// Two walls along X axis (dirX=1, dirZ=0) at z=0 and z=6.
    /// Two walls along Z axis (dirX=0, dirZ=1) at x=0 and x=4.
    func testComputeRoomDimensionsRectangularRoom() {
        let walls: [RoomPlanExporter.WallData] = [
            // Bottom wall: center at (2, 0), 4m wide along X
            .init(centerX: 2, centerZ: 0, halfWidth: 2, dirX: 1, dirZ: 0, height: 2.7),
            // Top wall: center at (2, 6), 4m wide along X
            .init(centerX: 2, centerZ: 6, halfWidth: 2, dirX: 1, dirZ: 0, height: 2.7),
            // Left wall: center at (0, 3), 6m wide along Z
            .init(centerX: 0, centerZ: 3, halfWidth: 3, dirX: 0, dirZ: 1, height: 2.7),
            // Right wall: center at (4, 3), 6m wide along Z
            .init(centerX: 4, centerZ: 3, halfWidth: 3, dirX: 0, dirZ: 1, height: 2.7),
        ]
        let (w, l, h) = RoomPlanExporter.computeRoomDimensions(walls)
        XCTAssertEqual(w, 4.0, "Room width should be 4m")
        XCTAssertEqual(l, 6.0, "Room length should be 6m")
        XCTAssertEqual(h, 2.7, "Room height should be 2.7m")
    }

    /// Single wall: bounding box is just that wall's extent.
    func testComputeRoomDimensionsSingleWall() {
        let walls: [RoomPlanExporter.WallData] = [
            .init(centerX: 5, centerZ: 3, halfWidth: 2, dirX: 1, dirZ: 0, height: 2.5)
        ]
        let (w, l, h) = RoomPlanExporter.computeRoomDimensions(walls)
        XCTAssertEqual(w, 4.0, "Width = 2 * halfWidth along X")
        XCTAssertEqual(l, 0.0, "No Z extent from a single X-aligned wall")
        XCTAssertEqual(h, 2.5)
    }

    /// Diagonal wall: 45-degree wall (dirX=0.707, dirZ=0.707) extends in both X and Z.
    func testComputeRoomDimensionsDiagonalWall() {
        let cos45: Float = 0.7071068
        let walls: [RoomPlanExporter.WallData] = [
            .init(centerX: 0, centerZ: 0, halfWidth: 2, dirX: cos45, dirZ: cos45, height: 3.0)
        ]
        let (w, l, h) = RoomPlanExporter.computeRoomDimensions(walls)
        // Extent in X and Z should each be halfWidth * cos(45°) ≈ 1.414
        XCTAssertEqual(w, 2.83, accuracy: 0.01, "Diagonal wall X extent")
        XCTAssertEqual(l, 2.83, accuracy: 0.01, "Diagonal wall Z extent")
        XCTAssertEqual(h, 3.0)
    }

    /// Walls at different heights: room height is the tallest wall.
    func testComputeRoomDimensionsMixedHeights() {
        let walls: [RoomPlanExporter.WallData] = [
            .init(centerX: 0, centerZ: 0, halfWidth: 1, dirX: 1, dirZ: 0, height: 2.4),
            .init(centerX: 0, centerZ: 2, halfWidth: 1, dirX: 1, dirZ: 0, height: 3.0),
            .init(centerX: 0, centerZ: 4, halfWidth: 1, dirX: 1, dirZ: 0, height: 2.7),
        ]
        let (_, _, h) = RoomPlanExporter.computeRoomDimensions(walls)
        XCTAssertEqual(h, 3.0, "Height should be the tallest wall")
    }

    /// Walls with center offsets: verifies bounding box accounts for wall position + extent.
    func testComputeRoomDimensionsOffsetWalls() {
        let walls: [RoomPlanExporter.WallData] = [
            // Wall centered at x=10, extending 1.5m each side → x: 8.5 to 11.5
            .init(centerX: 10, centerZ: 5, halfWidth: 1.5, dirX: 1, dirZ: 0, height: 2.7),
            // Wall centered at x=15, extending 2m each side → x: 13 to 17
            .init(centerX: 15, centerZ: 5, halfWidth: 2, dirX: 1, dirZ: 0, height: 2.7),
        ]
        let (w, _, _) = RoomPlanExporter.computeRoomDimensions(walls)
        XCTAssertEqual(w, 8.5, "Width = 17 - 8.5")
    }

    /// L-shaped room with walls at different orientations.
    func testComputeRoomDimensionsLShapedRoom() {
        let walls: [RoomPlanExporter.WallData] = [
            // Outer walls forming an L
            .init(centerX: 3, centerZ: 0, halfWidth: 3, dirX: 1, dirZ: 0, height: 2.7),  // bottom: x=0..6
            .init(centerX: 6, centerZ: 2, halfWidth: 2, dirX: 0, dirZ: 1, height: 2.7),  // right: z=0..4
            .init(centerX: 4.5, centerZ: 4, halfWidth: 1.5, dirX: 1, dirZ: 0, height: 2.7), // top-right: x=3..6
            .init(centerX: 3, centerZ: 5, halfWidth: 0, dirX: 0, dirZ: 1, height: 2.7),  // inner step
            .init(centerX: 1.5, centerZ: 6, halfWidth: 1.5, dirX: 1, dirZ: 0, height: 2.7), // top-left: x=0..3
            .init(centerX: 0, centerZ: 3, halfWidth: 3, dirX: 0, dirZ: 1, height: 2.7),  // left: z=0..6
        ]
        let (w, l, _) = RoomPlanExporter.computeRoomDimensions(walls)
        XCTAssertEqual(w, 6.0, "Bounding box width covers the full L")
        XCTAssertEqual(l, 6.0, "Bounding box length covers the full L")
    }

    /// Verifies that dimensions are rounded to 2 decimal places.
    func testComputeRoomDimensionsRounding() {
        let walls: [RoomPlanExporter.WallData] = [
            .init(centerX: 0, centerZ: 0, halfWidth: 1.333, dirX: 1, dirZ: 0, height: 2.555),
            .init(centerX: 3.777, centerZ: 0, halfWidth: 1.111, dirX: 1, dirZ: 0, height: 2.555),
        ]
        let (w, _, h) = RoomPlanExporter.computeRoomDimensions(walls)
        // Width: max_x = 3.777 + 1.111 = 4.888, min_x = 0 - 1.333 = -1.333 → 6.221
        XCTAssertEqual(w, 6.22, "Width should be rounded to 2 decimals")
        XCTAssertEqual(h, 2.56, "Height should be rounded to 2 decimals")
    }

    /// Room centered at negative coordinates (typical for RoomPlan, which uses
    /// the device's initial position as world origin).
    func testComputeRoomDimensionsNegativeCoordinates() {
        let walls: [RoomPlanExporter.WallData] = [
            .init(centerX: -3, centerZ: -5, halfWidth: 2, dirX: 1, dirZ: 0, height: 2.7),
            .init(centerX: -3, centerZ: -1, halfWidth: 2, dirX: 1, dirZ: 0, height: 2.7),
            .init(centerX: -5, centerZ: -3, halfWidth: 2, dirX: 0, dirZ: 1, height: 2.7),
            .init(centerX: -1, centerZ: -3, halfWidth: 2, dirX: 0, dirZ: 1, height: 2.7),
        ]
        let (w, l, h) = RoomPlanExporter.computeRoomDimensions(walls)
        XCTAssertEqual(w, 4.0, "Width should be 4m regardless of world position")
        XCTAssertEqual(l, 4.0, "Length should be 4m regardless of world position")
        XCTAssertEqual(h, 2.7)
    }

    /// Verifies round2 handles large Float values and documents Float precision behavior.
    /// Float(49.995) is stored as ~49.994999 due to 7-digit precision, so it rounds to 49.99.
    func testRound2LargeValues() {
        XCTAssertEqual(RoomPlanExporter.round2(999.999), 1000.0)
        XCTAssertEqual(RoomPlanExporter.round2(49.995), 49.99, "Float precision: 49.995 stored as ~49.994999")
        XCTAssertEqual(RoomPlanExporter.round2(50.0), 50.0)
    }

    /// Walls with zero halfWidth (degenerate case).
    func testComputeRoomDimensionsZeroWidthWalls() {
        let walls: [RoomPlanExporter.WallData] = [
            .init(centerX: 1, centerZ: 2, halfWidth: 0, dirX: 1, dirZ: 0, height: 2.7),
            .init(centerX: 5, centerZ: 8, halfWidth: 0, dirX: 0, dirZ: 1, height: 2.7),
        ]
        let (w, l, _) = RoomPlanExporter.computeRoomDimensions(walls)
        XCTAssertEqual(w, 4.0, "Width from center positions only")
        XCTAssertEqual(l, 6.0, "Length from center positions only")
    }

    // MARK: - Floor area computation

    /// Verifies floor area is width * length, rounded to 2 decimal places.
    func testFloorAreaComputation() {
        // Simulates what export() does: width * length rounded to 2 decimals
        let width = 4.2
        let length = 5.8
        let floorArea = ((width * length) * 100).rounded() / 100
        XCTAssertEqual(floorArea, 24.36)
    }

    /// Zero-dimension room produces zero floor area.
    func testFloorAreaZeroDimensions() {
        let width = 0.0
        let length = 0.0
        let floorArea = ((width * length) * 100).rounded() / 100
        XCTAssertEqual(floorArea, 0.0)
    }

    // MARK: - WallData struct

    /// Verifies WallData can be constructed with expected values.
    func testWallDataInitialization() {
        let data = RoomPlanExporter.WallData(
            centerX: 2.5, centerZ: 3.0,
            halfWidth: 1.5,
            dirX: 0.866, dirZ: 0.5,
            height: 2.7
        )
        XCTAssertEqual(data.centerX, 2.5)
        XCTAssertEqual(data.centerZ, 3.0)
        XCTAssertEqual(data.halfWidth, 1.5)
        XCTAssertEqual(data.dirX, 0.866)
        XCTAssertEqual(data.dirZ, 0.5)
        XCTAssertEqual(data.height, 2.7)
    }
}
