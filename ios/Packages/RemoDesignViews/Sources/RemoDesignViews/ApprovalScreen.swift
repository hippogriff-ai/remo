import SwiftUI
import RemoModels
import RemoNetworking

/// Design approval screen: review final design and approve for shopping list generation.
public struct ApprovalScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var isApproving = false
    @State private var errorMessage: String?

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        VStack(spacing: 20) {
            DesignImageView(projectState.currentImage)
                .aspectRatio(4/3, contentMode: .fit)
                .padding(.horizontal)

            Text("Iteration \(projectState.iterationCount) of 5")
                .font(.caption)
                .foregroundStyle(.secondary)

            Text("Happy with this design?")
                .font(.title3.bold())

            Text("Approving will generate your shopping list\nwith real products you can buy.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()

            Button {
                Task { await approve() }
            } label: {
                HStack(spacing: 8) {
                    if isApproving {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text(isApproving ? "Generating Shopping List..." : "Approve & Get Shopping List")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(isApproving)
            .padding(.horizontal)
            .padding(.bottom)
            .accessibilityLabel(isApproving ? "Generating shopping list" : "Approve design and get shopping list")
            .accessibilityHint("Finalizes your design and creates a list of matching products")
            .accessibilityIdentifier("approval_approve")
        }
        .navigationTitle("Review Design")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    private func approve() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("approve() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        isApproving = true
        defer { isApproving = false }
        do {
            try await client.approveDesign(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

#Preview {
    NavigationStack {
        ApprovalScreen(projectState: .preview(step: .approval), client: MockWorkflowClient(delay: .zero))
    }
}
