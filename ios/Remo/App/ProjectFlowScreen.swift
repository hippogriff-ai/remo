import SwiftUI
import RemoModels
import RemoNetworking

/// Drives the full project flow via NavigationStack.
/// Observes ProjectState and pushes the correct screen.
struct ProjectFlowScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var path: [ProjectStep] = []

    var body: some View {
        NavigationStack(path: $path) {
            ProjectRouter(step: projectState.step, projectState: projectState, client: client)
                .navigationDestination(for: ProjectStep.self) { step in
                    ProjectRouter(step: step, projectState: projectState, client: client)
                }
        }
        .onChange(of: projectState.step) { _, newStep in
            // Replace path with just the new step — workflow is linear, no backward navigation
            if path.last != newStep {
                path = [newStep]
            }
        }
        .overlay {
            if let error = projectState.error {
                ErrorOverlay(error: error) {
                    Task {
                        guard let projectId = projectState.projectId else { return }
                        do {
                            try await client.retryFailedStep(projectId: projectId)
                            let state = try await client.getState(projectId: projectId)
                            projectState.apply(state)
                        } catch is CancellationError {
                            // Task cancelled (e.g., view disappeared) — do nothing
                        } catch {
                            // Retry itself failed — keep showing the error overlay
                            // (projectState.error remains set)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Error Overlay

struct ErrorOverlay: View {
    let error: WorkflowError
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.largeTitle)
                .foregroundStyle(.orange)
            Text(error.message)
                .font(.headline)
                .multilineTextAlignment(.center)
            if error.retryable {
                Button("Tap to Retry", action: onRetry)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
        .padding()
    }
}

#Preview {
    let state = ProjectState()
    state.projectId = "preview-123"
    return ProjectFlowScreen(projectState: state, client: MockWorkflowClient())
}
