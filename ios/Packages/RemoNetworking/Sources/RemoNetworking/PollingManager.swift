import Foundation
import RemoModels

/// Polls GET /projects/{id} at a configurable interval.
/// Cancel-safe: stops when the Task is cancelled (view disappears).
public actor PollingManager {
    private let client: any WorkflowClientProtocol
    private let interval: Duration

    public init(client: any WorkflowClientProtocol, interval: Duration = .seconds(2)) {
        self.client = client
        self.interval = interval
    }

    /// Polls until the step changes from `currentStep` or the task is cancelled.
    /// Returns the new WorkflowState when a transition is detected.
    public func pollUntilStepChanges(projectId: String, currentStep: String) async throws -> WorkflowState {
        while !Task.isCancelled {
            try await Task.sleep(for: interval)
            let state = try await client.getState(projectId: projectId)
            if state.step != currentStep || state.error != nil {
                return state
            }
        }
        throw CancellationError()
    }

    /// Single poll — useful for manual refresh.
    public func poll(projectId: String) async throws -> WorkflowState {
        try await client.getState(projectId: projectId)
    }
}
