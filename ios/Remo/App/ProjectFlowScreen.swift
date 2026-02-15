import SwiftUI
import RemoModels
import RemoNetworking

/// Drives the full project flow by observing ProjectState.step.
/// Uses a direct view switch instead of a nested NavigationStack to avoid
/// the nested-NavigationStack crash in SwiftUI (EXC_BREAKPOINT in boundPathChange).
/// The outer NavigationStack from HomeScreen provides the navigation chrome.
struct ProjectFlowScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    private var isMockMode: Bool {
        client is MockWorkflowClient
    }

    var body: some View {
        ProjectRouter(step: projectState.step, projectState: projectState, client: client)
            .animation(.default, value: projectState.step)
            .safeAreaInset(edge: .bottom) {
                if isMockMode {
                    Text("Demo Mode — no real AI calls")
                        .font(.caption2.bold())
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 4)
                        .background(.orange.gradient, in: Capsule())
                        .padding(.bottom, 4)
                }
            }
            .overlay {
            if let error = projectState.error {
                ErrorOverlay(error: error) {
                    Task {
                        guard let projectId = projectState.projectId else {
                            assertionFailure("retry called without projectId")
                            return
                        }
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
