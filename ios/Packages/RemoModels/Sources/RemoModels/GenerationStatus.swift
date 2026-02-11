import Foundation

/// Tracks async activity status separately from navigation step.
/// Allows showing a loading spinner while the step hasn't changed yet.
public enum GenerationStatus: Codable, Hashable, Sendable {
    case idle
    case generating
    case completed
    case failed(String)
}
