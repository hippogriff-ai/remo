import SwiftUI
import RemoModels
import RemoNetworking

/// Loading screen shown while designs are being generated.
/// Polls the backend every 2s until the step advances or an error occurs.
public struct GeneratingScreen: View {
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

            Text("Creating your designs...")
                .font(.title3.bold())

            Text("This usually takes 15-30 seconds.\nWe're generating 2 unique options for you.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()
        }
        .padding()
        .navigationTitle("Generating")
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
                    currentStep: ProjectStep.generation.rawValue
                )
                projectState.apply(newState)
            } catch is CancellationError {
                // View disappeared — expected
            } catch {
                // Network error during polling — set error so ErrorOverlay shows
                projectState.error = WorkflowError(message: error.localizedDescription, retryable: true)
            }
        }
    }
}

#Preview {
    NavigationStack {
        GeneratingScreen(projectState: .preview(step: .generation), client: MockWorkflowClient(delay: .zero))
    }
}
