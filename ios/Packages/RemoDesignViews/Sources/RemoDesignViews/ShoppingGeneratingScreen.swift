import SwiftUI
import RemoModels
import RemoNetworking

/// Loading screen shown while the shopping list is being generated.
/// Polls the backend every 2s until the step advances to "completed" or an error occurs.
public struct ShoppingGeneratingScreen: View {
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

            Text("Building your shopping list...")
                .font(.title3.bold())

            Text("Finding matching products and\nchecking availability.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()
        }
        .padding()
        .navigationTitle("Shopping List")
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
                    currentStep: ProjectStep.shopping.rawValue
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
        ShoppingGeneratingScreen(projectState: .preview(step: .shopping), client: MockWorkflowClient(delay: .zero))
    }
}
