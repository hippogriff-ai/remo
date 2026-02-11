import Foundation

/// Maps 1:1 to the Temporal workflow step strings.
/// The backend returns step as a raw string; this enum provides type safety.
public enum ProjectStep: String, Codable, Hashable, CaseIterable, Sendable {
    case photoUpload = "photos"
    case scan = "scan"
    case intake = "intake"
    case generation = "generation"
    case selection = "selection"
    case iteration = "iteration"
    case approval = "approval"
    case shopping = "shopping"
    case completed = "completed"
}
