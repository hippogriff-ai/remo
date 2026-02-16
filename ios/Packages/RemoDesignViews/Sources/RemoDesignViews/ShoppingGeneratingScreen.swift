import SwiftUI
import RemoModels
import RemoNetworking
import RemoShoppingList

/// Loading screen shown while the shopping list is being generated.
/// Tries SSE streaming first (products appear one-by-one), falls back to polling.
public struct ShoppingGeneratingScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var streamingTask: Task<Void, Never>?
    @State private var statusMessage: String = "Building your shopping list..."
    @State private var statusDetail: String = "Finding matching products and\nchecking availability."
    @State private var streamedItems: [ProductMatch] = []
    @State private var receivedAnyEvent = false
    @State private var receivedDoneEvent = false
    @State private var receivedTerminalError = false

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        Group {
            if streamedItems.isEmpty {
                // Spinner phase — no products yet
                VStack(spacing: 24) {
                    Spacer()

                    ProgressView()
                        .scaleEffect(1.5)
                        .padding()

                    Text(statusMessage)
                        .font(.title3.bold())

                    Text(statusDetail)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)

                    Spacer()
                }
                .padding()
            } else {
                // Progressive product list — items arriving one by one
                List {
                    Section {
                        HStack(spacing: 8) {
                            ProgressView()
                            Text(statusMessage)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    }

                    ForEach(streamedItems, id: \.productUrl) { item in
                        ProductCard(item: item)
                            .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                }
                .animation(.easeInOut(duration: 0.3), value: streamedItems.count)
            }
        }
        .navigationTitle("Shopping List")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden()
        #endif
        .onAppear { startStreaming() }
        .onDisappear { streamingTask?.cancel() }
    }

    private func startStreaming() {
        guard let projectId = projectState.projectId else {
            assertionFailure("startStreaming() called without projectId")
            projectState.error = WorkflowError(message: "Project not initialized", retryable: false)
            return
        }

        streamingTask = Task {
            let stream = client.streamShopping(projectId: projectId)
            do {
                for try await event in stream {
                    receivedAnyEvent = true
                    handleEvent(event)
                }
                // Stream ended — fall back to polling if we never got a done
                // event and no terminal error was already surfaced.
                if !receivedDoneEvent && !receivedTerminalError {
                    await fallbackToPolling(projectId: projectId)
                }
            } catch is CancellationError {
                // View disappeared — expected
            } catch {
                // SSE failed — fall back to polling if stream hadn't completed
                // and no terminal error was already surfaced.
                if !receivedDoneEvent && !receivedTerminalError {
                    await fallbackToPolling(projectId: projectId)
                } else {
                    let apiError = error as? APIError
                    projectState.error = WorkflowError(
                        message: apiError?.errorDescription ?? error.localizedDescription,
                        retryable: apiError?.isRetryable ?? true
                    )
                }
            }
        }
    }

    private func handleEvent(_ event: ShoppingSSEEvent) {
        switch event {
        case .status(let phase, let itemCount):
            if let count = itemCount {
                statusMessage = "\(phase) — \(count) items to find"
            } else {
                statusMessage = phase
            }
            statusDetail = ""

        case .itemSearch(let itemName, let candidates):
            if let candidates {
                statusMessage = "Searching for \(itemName)... found \(candidates)"
            } else {
                statusMessage = "Searching for \(itemName)..."
            }

        case .item(let product):
            withAnimation {
                streamedItems.append(product)
            }

        case .error(let message):
            receivedTerminalError = true
            projectState.error = WorkflowError(message: message, retryable: true)

        case .done(let output):
            receivedDoneEvent = true
            // Apply the full shopping result to transition to completed
            let state = WorkflowState(
                step: "completed",
                photos: projectState.photos,
                scanData: projectState.scanData,
                designBrief: projectState.designBrief,
                generatedOptions: projectState.generatedOptions,
                selectedOption: projectState.selectedOption,
                currentImage: projectState.currentImage,
                revisionHistory: projectState.revisionHistory,
                iterationCount: projectState.iterationCount,
                shoppingList: output,
                approved: projectState.approved,
                chatHistoryKey: projectState.chatHistoryKey
            )
            projectState.apply(state)
        }
    }

    private func fallbackToPolling(projectId: String) async {
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
            let apiError = error as? APIError
            projectState.error = WorkflowError(
                message: apiError?.errorDescription ?? error.localizedDescription,
                retryable: apiError?.isRetryable ?? true
            )
        }
    }
}

#Preview {
    NavigationStack {
        ShoppingGeneratingScreen(projectState: .preview(step: .shopping), client: MockWorkflowClient(delay: .zero))
    }
}
