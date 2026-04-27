import Foundation

#if canImport(ARKit)
import ARKit
#endif

/// Public capability check — does this device support Apple's RoomPlan
/// mesh-based scanning (requires LiDAR)?
public enum LiDARCapability {
    public static var isSupported: Bool {
        #if canImport(ARKit)
        return ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
        #else
        return false
        #endif
    }
}
