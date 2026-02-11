import SwiftUI
import RemoModels
import RemoNetworking

/// Landing screen: shows pending projects and a "New Project" button.
/// Persists project IDs to UserDefaults so resume works across app restarts.
struct HomeScreen: View {
    let client: any WorkflowClientProtocol

    @State private var projects: [(id: String, state: ProjectState)] = []
    @State private var isCreating = false
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var navigationPath = NavigationPath()

    private static let projectIdsKey = "remo_project_ids"

    var body: some View {
        NavigationStack(path: $navigationPath) {
            Group {
                if isLoading {
                    ProgressView("Loading projects...")
                } else if projects.isEmpty {
                    ContentUnavailableView(
                        "No Projects Yet",
                        systemImage: "house.fill",
                        description: Text("Tap the button below to redesign your first room.")
                    )
                } else {
                    List {
                        ForEach(projects, id: \.id) { project in
                            Button {
                                navigationPath.append(project.id)
                            } label: {
                                ProjectRow(projectState: project.state)
                            }
                        }
                        .onDelete { indexSet in
                            deleteProjects(at: indexSet)
                        }
                    }
                }
            }
            .navigationTitle("Remo")
            .navigationDestination(for: String.self) { projectId in
                if let project = projects.first(where: { $0.id == projectId }) {
                    ProjectFlowScreen(projectState: project.state, client: client)
                } else {
                    ContentUnavailableView(
                        "Project Not Found",
                        systemImage: "exclamationmark.triangle",
                        description: Text("This project may have been deleted.")
                    )
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
                    .accessibilityLabel("New Project")
                }
            }
            .task {
                await loadAndRefreshProjects()
            }
        }
    }

    private func loadAndRefreshProjects() async {
        // Restore persisted project IDs
        let savedIds = UserDefaults.standard.stringArray(forKey: Self.projectIdsKey) ?? []
        if !savedIds.isEmpty {
            projects = savedIds.map { id in
                let state = ProjectState()
                state.projectId = id
                return (id: id, state: state)
            }
        }
        isLoading = false

        // Refresh state from backend concurrently, removing purged projects
        let projectsCopy = projects
        let results: [(index: Int, state: WorkflowState?)] = await withTaskGroup(
            of: (Int, WorkflowState?).self,
            returning: [(Int, WorkflowState?)].self
        ) { group in
            for i in projectsCopy.indices {
                group.addTask {
                    do {
                        let state = try await self.client.getState(projectId: projectsCopy[i].id)
                        return (i, state)
                    } catch let error as APIError {
                        if case .httpError(let code, _) = error, code == 404 {
                            return (i, nil) // Purged on server
                        }
                        return (i, WorkflowState(step: "")) // Keep project, show stale
                    } catch is CancellationError {
                        return (i, WorkflowState(step: "")) // Keep on cancel
                    } catch {
                        return (i, WorkflowState(step: "")) // Keep on unknown error
                    }
                }
            }
            var collected: [(Int, WorkflowState?)] = []
            for await result in group {
                collected.append(result)
            }
            return collected
        }

        // Apply results and remove purged projects
        var validIndices: [Int] = []
        for (index, state) in results {
            if let state, !state.step.isEmpty {
                projects[index].state.apply(state)
                validIndices.append(index)
            } else if state != nil {
                // Empty step = kept due to error, don't apply but keep
                validIndices.append(index)
            }
            // nil = 404 purged, not added to validIndices
        }
        validIndices.sort()
        if validIndices.count < projects.count {
            projects = validIndices.map { projects[$0] }
            persistProjectIds()
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
            persistProjectIds()
            navigationPath.append(projectId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func deleteProjects(at offsets: IndexSet) {
        let idsToDelete = offsets.map { projects[$0].id }
        projects.remove(atOffsets: offsets)
        persistProjectIds()
        for projectId in idsToDelete {
            Task {
                do {
                    try await client.deleteProject(projectId: projectId)
                } catch is CancellationError {
                    // Ignore cancellation
                } catch {
                    errorMessage = "Failed to delete project from server: \(error.localizedDescription)"
                }
            }
        }
    }

    private func persistProjectIds() {
        let ids = projects.map(\.id)
        UserDefaults.standard.set(ids, forKey: Self.projectIdsKey)
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
