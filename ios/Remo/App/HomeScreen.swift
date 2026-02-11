import SwiftUI
import RemoModels
import RemoNetworking

/// Landing screen: shows pending projects and a "New Project" button.
struct HomeScreen: View {
    let client: any WorkflowClientProtocol

    @State private var projects: [(id: String, state: ProjectState)] = []
    @State private var isCreating = false
    @State private var errorMessage: String?
    @State private var navigationPath = NavigationPath()

    var body: some View {
        NavigationStack(path: $navigationPath) {
            List {
                if projects.isEmpty {
                    ContentUnavailableView(
                        "No Projects Yet",
                        systemImage: "house.fill",
                        description: Text("Tap the button below to redesign your first room.")
                    )
                } else {
                    ForEach(projects, id: \.id) { project in
                        Button {
                            navigationPath.append(project.id)
                        } label: {
                            ProjectRow(projectState: project.state)
                        }
                    }
                }
            }
            .navigationTitle("Remo")
            .navigationDestination(for: String.self) { projectId in
                if let project = projects.first(where: { $0.id == projectId }) {
                    ProjectFlowScreen(projectState: project.state, client: client)
                }
            }
            .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await createProject() }
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title2)
                    }
                    .disabled(isCreating)
                }
            }
        }
    }

    private func createProject() async {
        isCreating = true
        defer { isCreating = false }
        do {
            #if os(iOS)
            let fingerprint = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString
            #else
            let fingerprint = UUID().uuidString
            #endif
            let hasLidar = checkLiDARAvailability()
            let projectId = try await client.createProject(
                deviceFingerprint: fingerprint,
                hasLidar: hasLidar
            )
            let state = ProjectState()
            state.projectId = projectId
            projects.append((id: projectId, state: state))
            navigationPath.append(projectId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func checkLiDARAvailability() -> Bool {
        // RoomPlan availability check — requires ARKit at runtime
        // Placeholder: returns false. Real check in RemoLiDAR package.
        false
    }
}

// MARK: - Project Row

struct ProjectRow: View {
    let projectState: ProjectState

    var body: some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.secondary.opacity(0.2))
                .frame(width: 56, height: 56)
                .overlay {
                    Image(systemName: iconForStep(projectState.step))
                        .font(.title2)
                        .foregroundStyle(.secondary)
                }

            VStack(alignment: .leading, spacing: 4) {
                Text(titleForStep(projectState.step))
                    .font(.headline)
                Text(subtitleForStep(projectState.step))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 4)
    }

    private func iconForStep(_ step: ProjectStep) -> String {
        switch step {
        case .photoUpload: return "camera"
        case .scan: return "cube.transparent"
        case .intake: return "bubble.left.and.bubble.right"
        case .generation: return "wand.and.stars"
        case .selection: return "photo.on.rectangle.angled"
        case .iteration: return "pencil.and.outline"
        case .approval: return "checkmark.seal"
        case .shopping: return "cart"
        case .completed: return "checkmark.circle.fill"
        }
    }

    private func titleForStep(_ step: ProjectStep) -> String {
        switch step {
        case .photoUpload: return "Upload Photos"
        case .scan: return "Room Scan"
        case .intake: return "Design Chat"
        case .generation: return "Generating..."
        case .selection: return "Choose Design"
        case .iteration: return "Refine Design"
        case .approval: return "Review Design"
        case .shopping: return "Shopping List"
        case .completed: return "Complete"
        }
    }

    private func subtitleForStep(_ step: ProjectStep) -> String {
        switch step {
        case .photoUpload: return "Take photos of your room"
        case .scan: return "Scan room dimensions"
        case .intake: return "Tell us your style"
        case .generation: return "Creating your designs..."
        case .selection: return "Pick your favorite"
        case .iteration: return "Fine-tune details"
        case .approval: return "Approve final design"
        case .shopping: return "Browse matching products"
        case .completed: return "Your design is ready!"
        }
    }
}

#Preview {
    HomeScreen(client: MockWorkflowClient())
}
