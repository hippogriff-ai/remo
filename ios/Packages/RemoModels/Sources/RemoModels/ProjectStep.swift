import Foundation

/// Maps 1:1 to the Temporal workflow step strings.
/// The backend returns step as a raw string; this enum provides type safety.
/// Conforms to `Comparable` based on workflow progression order.
public enum ProjectStep: String, Codable, Hashable, CaseIterable, Sendable, Comparable {
    case photoUpload = "photos"
    case scan = "scan"
    case intake = "intake"
    case generation = "generation"
    case selection = "selection"
    case iteration = "iteration"
    case approval = "approval"
    case shopping = "shopping"
    case completed = "completed"
    case abandoned = "abandoned"
    case cancelled = "cancelled"

    /// Whether this step is a terminal state (project has ended).
    public var isTerminal: Bool {
        switch self {
        case .completed, .abandoned, .cancelled: true
        default: false
        }
    }

    /// Ordinal position in the workflow flow (0-based).
    /// Terminal states sort after completed.
    private var ordinal: Int {
        switch self {
        case .photoUpload: 0
        case .scan: 1
        case .intake: 2
        case .generation: 3
        case .selection: 4
        case .iteration: 5
        case .approval: 6
        case .shopping: 7
        case .completed: 8
        case .abandoned: 9
        case .cancelled: 10
        }
    }

    public static func < (lhs: ProjectStep, rhs: ProjectStep) -> Bool {
        lhs.ordinal < rhs.ordinal
    }
}
