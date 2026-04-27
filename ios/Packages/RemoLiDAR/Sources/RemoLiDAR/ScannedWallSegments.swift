import Foundation

#if canImport(RoomPlan)
import RoomPlan
import simd
#endif

/// A wall laid out in the room's top-down floor plan. All values in meters,
/// world space (y axis is height, ignored). start / end are the two ends of
/// the wall along its top-down trace.
public struct ScannedWallSegment: Sendable, Hashable {
    public let patchId: String
    public let startX: Double
    public let startZ: Double
    public let endX: Double
    public let endZ: Double

    public init(patchId: String, startX: Double, startZ: Double, endX: Double, endZ: Double) {
        self.patchId = patchId
        self.startX = startX; self.startZ = startZ
        self.endX = endX; self.endZ = endZ
    }
}

public enum WallSegmentsExtractor {
    #if canImport(RoomPlan)
    /// Pulls world-space wall endpoints from the CapturedRoom so iOS can
    /// render a correct floor plan (not a synthetic "turn right every time"
    /// approximation). IDs match RoomPlanExporter's wall_0, wall_1, etc.
    public static func extract(from room: CapturedRoom) -> [ScannedWallSegment] {
        room.walls.enumerated().map { (idx, wall) in
            let center = wall.transform.columns.3
            // Local X axis of the wall in world space (y=0 projection).
            let axisX = simd_float3(wall.transform.columns.0.x, 0, wall.transform.columns.0.z)
            let len = simd_length(axisX)
            let unit = len > 1e-5 ? axisX / len : simd_float3(1, 0, 0)
            let half = abs(wall.dimensions.x) / 2
            let startWorld = simd_float3(center.x, 0, center.z) - unit * half
            let endWorld = simd_float3(center.x, 0, center.z) + unit * half
            return ScannedWallSegment(
                patchId: "wall_\(idx)",
                startX: Double(startWorld.x),
                startZ: Double(startWorld.z),
                endX: Double(endWorld.x),
                endZ: Double(endWorld.z)
            )
        }
    }
    #endif
}
