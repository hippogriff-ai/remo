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
            // Design image placeholder
            RoundedRectangle(cornerRadius: 12)
                .fill(Color.secondary.opacity(0.1))
                .aspectRatio(4/3, contentMode: .fit)
                .overlay {
                    VStack {
                        Image(systemName: "checkmark.seal")
                            .font(.system(size: 40))
                            .foregroundStyle(.green)
                        Text("Final Design")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
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
        .navigationBarTitleDisplayMode(.inline)
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
