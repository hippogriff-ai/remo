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
    @State private var showOnboardingTip = false

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
                    .accessibilityIdentifier("home_empty_state")
                } else {
                    List {
                        ForEach(Array(projects.enumerated()), id: \.element.id) { index, project in
                            NavigationLink(value: project.id) {
                                ProjectRow(projectState: project.state)
                            }
                            .accessibilityIdentifier("home_project_\(index)")
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
                if !projects.isEmpty {
                    ToolbarItem(placement: .cancellationAction) {
                        EditButton()
                            .accessibilityIdentifier("home_edit")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await createProject() }
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title2)
                    }
                    .disabled(isCreating)
                    .accessibilityLabel("New Project")
                    .accessibilityIdentifier("home_new_project")
                }
            }
            .task {
                // Show onboarding tooltip on first launch
                let isMaestroTest = UserDefaults.standard.bool(forKey: "maestro-test")
                let hasSeenOnboarding = UserDefaults.standard.bool(forKey: "remo_has_seen_onboarding")
                if !hasSeenOnboarding && !isMaestroTest {
                    showOnboardingTip = true
                    UserDefaults.standard.set(true, forKey: "remo_has_seen_onboarding")
                }
                await loadAndRefreshProjects()
            }
            .alert("Welcome to Remo", isPresented: $showOnboardingTip) {
                Button("Got It") {}
            } message: {
                Text("Your design data is temporary — save your final image to Photos when you're done. We automatically delete all project data within 48 hours.")
            }
        }
    }

    private func loadAndRefreshProjects() async {
        // Restore persisted project IDs
        // Note: chatMessages/currentIntakeOutput are not persisted — on relaunch,
        // intake-step projects restart the conversation. In P2 with the real backend,
        // chat history can be fetched via WorkflowState.chatHistoryKey.
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
        let results: [(id: String, state: WorkflowState?)] = await withTaskGroup(
            of: (String, WorkflowState?).self,
            returning: [(String, WorkflowState?)].self
        ) { group in
            for project in projectsCopy {
                group.addTask {
                    do {
                        let state = try await self.client.getState(projectId: project.id)
                        return (project.id, state)
                    } catch let error as APIError {
                        if case .httpError(let code, _) = error, code == 404 {
                            return (project.id, nil) // Purged on server
                        }
                        return (project.id, WorkflowState(step: "")) // Keep project, show stale
                    } catch is CancellationError {
                        return (project.id, WorkflowState(step: "")) // Keep on cancel
                    } catch {
                        return (project.id, WorkflowState(step: "")) // Keep on unknown error
                    }
                }
            }
            var collected: [(String, WorkflowState?)] = []
            for await result in group {
                collected.append(result)
            }
            return collected
        }

        // Apply results keyed by projectId (safe against concurrent mutations)
        var purgedIds: Set<String> = []
        for (projectId, state) in results {
            guard let index = projects.firstIndex(where: { $0.id == projectId }) else { continue }
            if let state, !state.step.isEmpty {
                projects[index].state.apply(state)
                // Remove terminal projects from the active list
                if let step = ProjectStep(rawValue: state.step), step.isTerminal {
                    purgedIds.insert(projectId)
                }
            } else if state == nil {
                purgedIds.insert(projectId)
            }
        }
        if !purgedIds.isEmpty {
            projects.removeAll { purgedIds.contains($0.id) }
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
            // Persist ID before getState so the project survives a transient failure
            projects.append((id: projectId, state: state))
            persistProjectIds()
            do {
                let workflowState = try await client.getState(projectId: projectId)
                state.apply(workflowState)
            } catch {
                // Project is saved; state will refresh on next app launch
            }
            navigationPath.append(projectId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func deleteProjects(at offsets: IndexSet) {
        // Capture removed projects for rollback on failure
        let removed = offsets.map { projects[$0] }
        projects.remove(atOffsets: offsets)
        persistProjectIds()
        for project in removed {
            Task {
                do {
                    try await client.deleteProject(projectId: project.id)
                } catch is CancellationError {
                    // Ignore cancellation
                } catch {
                    // Restore project on failure so it's not lost from UserDefaults
                    projects.append(project)
                    persistProjectIds()
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
                HStack(spacing: 6) {
                    Text(titleForStep(projectState.step))
                        .font(.headline)
                    if projectState.step != .completed {
                        Text("Resume")
                            .font(.caption2.bold())
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.accentColor.opacity(0.15))
                            .foregroundStyle(Color.accentColor)
                            .clipShape(Capsule())
                            .accessibilityIdentifier("resume_badge")
                    }
                }
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
        case .abandoned: return "clock.badge.xmark"
        case .cancelled: return "xmark.circle"
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
        case .abandoned: return "Expired"
        case .cancelled: return "Cancelled"
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
        case .abandoned: return "Deleted after 48h inactivity"
        case .cancelled: return "This project was cancelled"
        }
    }
}

#Preview {
    HomeScreen(client: MockWorkflowClient())
}
