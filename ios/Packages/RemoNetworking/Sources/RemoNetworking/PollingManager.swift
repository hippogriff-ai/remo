import Foundation
import RemoModels

/// Polls GET /projects/{id} at a configurable interval.
/// Cancel-safe: stops when the Task is cancelled (view disappears).
/// Retries transient errors up to `maxRetries` with exponential backoff.
public actor PollingManager {
    private let client: any WorkflowClientProtocol
    private let interval: Duration
    private let maxRetries: Int

    public init(client: any WorkflowClientProtocol, interval: Duration = .seconds(2), maxRetries: Int = 3) {
        self.client = client
        self.interval = interval
        self.maxRetries = maxRetries
    }

    /// Polls until the step changes from `currentStep` or the task is cancelled.
    /// Returns the new WorkflowState when a transition is detected.
    public func pollUntilStepChanges(projectId: String, currentStep: String) async throws -> WorkflowState {
        var consecutiveErrors = 0
        while !Task.isCancelled {
            // On retry, backoff sleep already happened — skip the normal interval
            if consecutiveErrors == 0 {
                try await Task.sleep(for: interval)
            }
            do {
                let state = try await client.getState(projectId: projectId)
                consecutiveErrors = 0
                if state.step != currentStep || state.error != nil {
                    return state
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as APIError where error.isCancellation {
                // URLSession cancellation wrapped as APIError — treat as cancellation
                throw CancellationError()
            } catch let error as APIError where error.isRetryable {
                consecutiveErrors += 1
                if consecutiveErrors > maxRetries {
                    throw error
                }
                // Exponential backoff based on poll interval: interval×2, interval×4, interval×8
                let multiplier = pow(2.0, Double(consecutiveErrors))
                let backoffNanos = Double(interval.components.seconds) * 1_000_000_000
                    + Double(interval.components.attoseconds) / 1_000_000_000
                let backoff = Duration.nanoseconds(Int64(backoffNanos * multiplier))
                try await Task.sleep(for: backoff)
            } catch {
                // Non-retryable error — fail immediately
                throw error
            }
        }
        throw CancellationError()
    }

    /// Polls until a custom condition is satisfied or an error appears.
    /// Use this when the step doesn't change but other state fields do
    /// (e.g., iterationCount increments during edit processing).
    public func pollUntil(projectId: String, condition: @escaping @Sendable (WorkflowState) -> Bool) async throws -> WorkflowState {
        var consecutiveErrors = 0
        while !Task.isCancelled {
            if consecutiveErrors == 0 {
                try await Task.sleep(for: interval)
            }
            do {
                let state = try await client.getState(projectId: projectId)
                consecutiveErrors = 0
                if condition(state) || state.error != nil {
                    return state
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as APIError where error.isCancellation {
                throw CancellationError()
            } catch let error as APIError where error.isRetryable {
                consecutiveErrors += 1
                if consecutiveErrors > maxRetries {
                    throw error
                }
                let multiplier = pow(2.0, Double(consecutiveErrors))
                let backoffNanos = Double(interval.components.seconds) * 1_000_000_000
                    + Double(interval.components.attoseconds) / 1_000_000_000
                let backoff = Duration.nanoseconds(Int64(backoffNanos * multiplier))
                try await Task.sleep(for: backoff)
            } catch {
                throw error
            }
        }
        throw CancellationError()
    }

    /// Single poll — useful for manual refresh.
    public func poll(projectId: String) async throws -> WorkflowState {
        try await client.getState(projectId: projectId)
    }
}
