import Foundation
import simd

#if canImport(RoomPlan)
import RoomPlan
#endif

/// Converts Apple RoomPlan CapturedRoom into the [String: Any] dict the backend expects.
///
/// The export path is: CapturedRoom → export() → [String: Any] → uploadScan().
/// Dimension computation and rounding helpers are outside the RoomPlan guard for testability.
public struct RoomPlanExporter {

    // MARK: - Testable types and helpers (no RoomPlan dependency)

    /// Wall geometry extracted from CapturedRoom.Surface transforms.
    /// Separated from RoomPlan types so dimension computation is testable on macOS.
    struct WallData {
        let centerX: Float
        let centerZ: Float
        let halfWidth: Float
        /// X component of the wall's local X-axis direction in world space.
        let dirX: Float
        /// Z component of the wall's local X-axis direction in world space.
        let dirZ: Float
        let height: Float
    }

    /// Round a Float to 2 decimal places, returning Double for JSON serialization.
    /// Converts to Double before rounding to avoid Float precision artifacts.
    static func round2(_ value: Float) -> Double {
        (Double(value) * 100).rounded() / 100
    }

    /// Compute room width, length, and height from wall bounding box.
    ///
    /// Each wall extends `halfWidth` along its local X axis (given by dirX, dirZ).
    /// Width = extent along world X axis. Length = extent along world Z axis.
    /// Height = tallest wall.
    static func computeRoomDimensions(_ walls: [WallData]) -> (width: Double, length: Double, height: Double) {
        guard !walls.isEmpty else { return (0, 0, 0) }
        var minX: Float = .infinity, maxX: Float = -.infinity
        var minZ: Float = .infinity, maxZ: Float = -.infinity
        var maxHeight: Float = 0
        for w in walls {
            let extX = w.halfWidth * abs(w.dirX)
            let extZ = w.halfWidth * abs(w.dirZ)
            minX = min(minX, w.centerX - extX)
            maxX = max(maxX, w.centerX + extX)
            minZ = min(minZ, w.centerZ - extZ)
            maxZ = max(maxZ, w.centerZ + extZ)
            maxHeight = max(maxHeight, w.height)
        }
        return (round2(maxX - minX), round2(maxZ - minZ), round2(maxHeight))
    }

    // MARK: - CapturedRoom export (iOS only)

    #if canImport(RoomPlan)

    /// Convert a CapturedRoom to the [String: Any] dict the backend scan endpoint expects.
    ///
    /// In RoomPlan's API, walls/doors/windows/openings/floors are all `CapturedRoom.Surface`
    /// differentiated by `.category`. Objects (furniture) are `CapturedRoom.Object`.
    /// Omits fields Apple doesn't provide: surface material, opening wall_id.
    /// All dimensions are in meters, rounded to 2 decimal places.
    public static func export(_ room: CapturedRoom) -> [String: Any] {
        let wallData = extractWallData(room.walls)
        let (width, length, height) = computeRoomDimensions(wallData)
        let floorArea = ((width * length) * 100).rounded() / 100

        return [
            "room": [
                "width": width,
                "length": length,
                "height": height,
                "unit": "meters"
            ] as [String: Any],
            "walls": exportWalls(room.walls),
            "openings": exportOpenings(room.doors + room.windows + room.openings, walls: room.walls),
            "furniture": exportObjects(room.objects),
            "surfaces": exportSurfaces(room.floors),
            "floor_area_sqm": floorArea
        ]
    }

    // MARK: - Private helpers

    private static func extractWallData(_ walls: [CapturedRoom.Surface]) -> [WallData] {
        walls.map { wall in
            WallData(
                centerX: wall.transform.columns.3.x,
                centerZ: wall.transform.columns.3.z,
                halfWidth: abs(wall.dimensions.x) / 2,
                dirX: wall.transform.columns.0.x,
                dirZ: wall.transform.columns.0.z,
                height: abs(wall.dimensions.y)
            )
        }
    }

    private static func exportWalls(_ walls: [CapturedRoom.Surface]) -> [[String: Any]] {
        walls.enumerated().map { index, wall in
            var dict: [String: Any] = [
                "id": "wall_\(index)",
                "width": round2(abs(wall.dimensions.x)),
                "height": round2(abs(wall.dimensions.y))
            ]
            let radians = atan2(wall.transform.columns.0.z, wall.transform.columns.0.x)
            dict["orientation"] = round2(radians * 180.0 / .pi)
            return dict
        }
    }

    private static func exportOpenings(
        _ openings: [CapturedRoom.Surface],
        walls: [CapturedRoom.Surface]
    ) -> [[String: Any]] {
        // Build wall_id lookup keyed by UUID. RoomPlan sets parentIdentifier on
        // each opening to its containing wall's identifier.
        let wallIndex: [UUID: (Int, CapturedRoom.Surface)] = Dictionary(
            uniqueKeysWithValues: walls.enumerated().map { ($0.1.identifier, ($0.0, $0.1)) }
        )

        return openings.map { opening in
            var dict: [String: Any] = [
                "type": mapSurfaceToOpeningType(opening.category),
                "width": round2(abs(opening.dimensions.x)),
                "height": round2(abs(opening.dimensions.y)),
            ]

            // Match opening to parent wall (required by tile mode's
            // patches_from_room_dimensions to project as a Hole on the wall).
            if let parentID = opening.parentIdentifier,
               let (wallIdx, wall) = wallIndex[parentID] {
                dict["wall_id"] = "wall_\(wallIdx)"
                let (px, py) = projectOntoWall(opening: opening, wall: wall)
                dict["position"] = [
                    "x": round2(px),
                    "y": round2(py),
                ] as [String: Any]
            }
            return dict
        }
    }

    /// Return the opening's (x, y) top-left in the wall's surface-local 2D
    /// meters — x along the wall's width axis from the left edge, y from the
    /// bottom of the wall. Used to produce a backend-compatible position dict.
    private static func projectOntoWall(
        opening: CapturedRoom.Surface, wall: CapturedRoom.Surface
    ) -> (Float, Float) {
        let wallPos = wall.transform.columns.3
        let openingPos = opening.transform.columns.3
        // Horizontal axis of the wall in world space (ignoring any tilt).
        let wallAxisX = simd_float3(
            wall.transform.columns.0.x, 0, wall.transform.columns.0.z
        )
        let lenX = simd_length(wallAxisX)
        guard lenX > 1e-5 else { return (0, 0) }
        let unitX = wallAxisX / lenX
        let delta = simd_float3(
            openingPos.x - wallPos.x, 0, openingPos.z - wallPos.z
        )
        let centerOffsetX = simd_dot(delta, unitX)
        let halfWallWidth = abs(wall.dimensions.x) / 2
        let halfWallHeight = abs(wall.dimensions.y) / 2
        let positionX = halfWallWidth + centerOffsetX - abs(opening.dimensions.x) / 2
        let wallBottomY = wallPos.y - halfWallHeight
        let positionY = openingPos.y - abs(opening.dimensions.y) / 2 - wallBottomY
        return (positionX, positionY)
    }

    private static func exportObjects(_ objects: [CapturedRoom.Object]) -> [[String: Any]] {
        objects.map { obj in
            [
                "type": mapObjectCategory(obj.category),
                "width": round2(abs(obj.dimensions.x)),
                "depth": round2(abs(obj.dimensions.z)),
                "height": round2(abs(obj.dimensions.y))
            ] as [String: Any]
        }
    }

    private static func exportSurfaces(_ floors: [CapturedRoom.Surface]) -> [[String: Any]] {
        floors.map { _ in
            ["type": "floor"] as [String: Any]
        }
    }

    /// Map a Surface.Category to the backend's opening type string.
    /// Doors and windows come from `room.doors` and `room.windows`;
    /// generic openings from `room.openings`.
    private static func mapSurfaceToOpeningType(_ category: CapturedRoom.Surface.Category) -> String {
        switch category {
        case .door: return "door"          // .door(isOpen: Bool) — associated value ignored
        case .window: return "window"
        case .opening: return "opening"
        default: return "opening"          // .wall, .floor shouldn't appear here but handle gracefully
        }
    }

    private static func mapObjectCategory(_ category: CapturedRoom.Object.Category) -> String {
        switch category {
        case .storage: return "storage"
        case .refrigerator: return "refrigerator"
        case .stove: return "stove"
        case .bed: return "bed"
        case .sink: return "sink"
        case .washerDryer: return "washer_dryer"
        case .toilet: return "toilet"
        case .bathtub: return "bathtub"
        case .oven: return "oven"
        case .dishwasher: return "dishwasher"
        case .table: return "table"
        case .sofa: return "sofa"
        case .chair: return "chair"
        case .fireplace: return "fireplace"
        case .television: return "television"
        case .stairs: return "stairs"
        @unknown default: return "unknown"
        }
    }

    #endif
}
