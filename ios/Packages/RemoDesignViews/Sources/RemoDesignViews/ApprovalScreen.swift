import SwiftUI
import RemoModels

/// Design approval screen: review final design and approve for shopping list generation.
public struct ApprovalScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var isApproving = false

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
                Text("Approve & Get Shopping List")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(isApproving)
            .padding(.horizontal)
            .padding(.bottom)
        }
        .navigationTitle("Review Design")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }

    private func approve() async {
        guard let projectId = projectState.projectId else { return }
        isApproving = true
        defer { isApproving = false }
        do {
            try await client.approveDesign(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            // TODO: error handling
        }
    }
}
