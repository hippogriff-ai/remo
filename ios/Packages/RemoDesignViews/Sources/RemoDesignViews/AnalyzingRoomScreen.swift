import SwiftUI
import RemoModels
import RemoNetworking

/// Loading screen shown while the backend analyzes room photos.
/// Polls the backend every 2s until the step advances past "analyzing".
public struct AnalyzingRoomScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var pollingTask: Task<Void, Never>?

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        VStack(spacing: 24) {
            Spacer()

            ProgressView()
                .scaleEffect(1.5)
                .padding()

            Text("Analyzing your room...")
                .font(.title3.bold())

            Text("This takes 60–90 seconds.\nWe're studying your photos to understand the space — be right back!")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()
        }
        .padding()
        .navigationTitle("Analyzing")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden()
        #endif
        .onAppear { startPolling() }
        .onDisappear { pollingTask?.cancel() }
    }

    private func startPolling() {
        guard let projectId = projectState.projectId else {
            assertionFailure("startPolling() called without projectId")
            projectState.error = WorkflowError(message: "Project not initialized", retryable: false)
            return
        }
        pollingTask = Task {
            let poller = PollingManager(client: client)
            do {
                let newState = try await poller.pollUntilStepChanges(
                    projectId: projectId,
                    currentStep: ProjectStep.analyzing.rawValue
                )
                projectState.apply(newState)
            } catch is CancellationError {
                // View disappeared — expected
            } catch {
                projectState.error = WorkflowError(message: error.localizedDescription, retryable: true)
            }
        }
    }
}

#Preview {
    NavigationStack {
        AnalyzingRoomScreen(projectState: .preview(step: .analyzing), client: MockWorkflowClient(delay: .zero))
    }
}
