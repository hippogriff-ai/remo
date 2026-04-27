import SwiftUI
import RemoModels
import RemoNetworking
import RemoTileMode

/// Landing screen: shows pending projects and a "New Project" button.
/// Persists project IDs to UserDefaults so resume works across app restarts.
struct HomeScreen: View {
    let client: any WorkflowClientProtocol
    let tileClient: any TileWorkflowClient

    @State private var projects: [(id: String, state: ProjectState)] = []
    @State private var tileProjectIds: [String] = []
    @State private var isCreating = false
    @State private var isCreatingTile = false
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
                } else {
                    VStack(spacing: 0) {
                        // Two big action cards at the top
                        HStack(spacing: 12) {
                            HomeActionCard(
                                title: "New Project",
                                subtitle: "Redesign a room",
                                systemImage: "sparkles",
                                style: .outlined,
                                isLoading: isCreating,
                                identifier: "home_new_project"
                            ) {
                                Task { await createProject() }
                            }
                            HomeActionCard(
                                title: "Replace Material",
                                subtitle: "Swap tile only",
                                systemImage: "square.grid.2x2.fill",
                                style: .filled,
                                isLoading: isCreatingTile,
                                identifier: "home_new_tile_project"
                            ) {
                                Task { await createTileProject() }
                            }
                        }
                        .padding(.horizontal, 20)
                        .padding(.top, 12)

                        if projects.isEmpty && tileProjectIds.isEmpty {
                            ContentUnavailableView(
                                "No Projects Yet",
                                systemImage: "house.fill",
                                description: Text("Tap one of the cards above to start.")
                            )
                            .accessibilityIdentifier("home_empty_state")
                        } else {
                            List {
                                if !tileProjectIds.isEmpty {
                                    Section("Tile projects") {
                                        ForEach(Array(tileProjectIds.enumerated()), id: \.element) { index, id in
                                            NavigationLink(value: HomeDestination.tileProject(id)) {
                                                HStack {
                                                    Image(systemName: "square.grid.2x2.fill")
                                                        .foregroundStyle(.blue)
                                                    VStack(alignment: .leading, spacing: 2) {
                                                        Text("Replace Material").font(.headline)
                                                        Text("Tap to resume").font(.caption).foregroundStyle(.secondary)
                                                    }
                                                }
                                            }
                                            .accessibilityIdentifier("tile_project_\(index)")
                                        }
                                        .onDelete { indexSet in
                                            deleteTileProjects(at: indexSet)
                                        }
                                    }
                                }
                                if !projects.isEmpty {
                                    Section("Design projects") {
                                        ForEach(Array(projects.enumerated()), id: \.element.id) { index, project in
                                            NavigationLink(value: HomeDestination.designProject(project.id)) {
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
                        }
                    }
                }
            }
            .navigationTitle("Remo")
            .navigationDestination(for: HomeDestination.self) { dest in
                switch dest {
                case .designProject(let projectId):
                    if let project = projects.first(where: { $0.id == projectId }) {
                        ProjectFlowScreen(projectState: project.state, client: client)
                    } else {
                        ContentUnavailableView(
                            "Project Not Found",
                            systemImage: "exclamationmark.triangle",
                            description: Text("This project may have been deleted.")
                        )
                    }
                case .tileProject(let projectId):
                    TileProjectFlowScreen(projectId: projectId, client: tileClient)
                }
            }
            .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
            .toolbar {
                if !projects.isEmpty || !tileProjectIds.isEmpty {
                    ToolbarItem(placement: .cancellationAction) {
                        EditButton()
                            .accessibilityIdentifier("home_edit")
                    }
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
                loadTileProjects()
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

    // MARK: - Tile projects

    private static let tileProjectIdsKey = "remo_tile_project_ids"

    private func loadTileProjects() {
        tileProjectIds = UserDefaults.standard.stringArray(forKey: Self.tileProjectIdsKey) ?? []
    }

    private func createTileProject() async {
        isCreatingTile = true
        defer { isCreatingTile = false }
        do {
            #if os(iOS)
            let fingerprint = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString
            #else
            let fingerprint = UUID().uuidString
            #endif
            let id = try await tileClient.createProject(deviceFingerprint: fingerprint)
            tileProjectIds.append(id)
            UserDefaults.standard.set(tileProjectIds, forKey: Self.tileProjectIdsKey)
            navigationPath.append(HomeDestination.tileProject(id))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func deleteTileProjects(at offsets: IndexSet) {
        let removed = offsets.map { tileProjectIds[$0] }
        tileProjectIds.remove(atOffsets: offsets)
        UserDefaults.standard.set(tileProjectIds, forKey: Self.tileProjectIdsKey)
        for id in removed {
            Task { try? await tileClient.cancelProject(projectId: id) }
        }
    }
}

enum HomeDestination: Hashable {
    case designProject(String)
    case tileProject(String)
}

struct HomeActionCard: View {
    let title: String
    let subtitle: String
    let systemImage: String
    let style: Style
    let isLoading: Bool
    let identifier: String
    let action: () -> Void

    enum Style { case outlined, filled }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: systemImage)
                        .font(.title2)
                    Spacer()
                    if isLoading { ProgressView().tint(style == .filled ? .white : .accentColor) }
                }
                Spacer()
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline)
                    Text(subtitle).font(.caption).opacity(0.8)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 110, alignment: .leading)
            .padding(14)
            .background(style == .filled ? Color.accentColor : Color(.systemBackground))
            .foregroundStyle(style == .filled ? Color.white : Color.primary)
            .overlay(
                RoundedRectangle(cornerRadius: 18)
                    .stroke(style == .filled ? Color.clear : Color.accentColor, lineWidth: 1.5)
            )
            .clipShape(RoundedRectangle(cornerRadius: 18))
        }
        .buttonStyle(.plain)
        .disabled(isLoading)
        .accessibilityIdentifier(identifier)
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
        case .analyzing: return "eye.circle"
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
        case .analyzing: return "Analyzing Room"
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
        case .analyzing: return "Understanding your space..."
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
    HomeScreen(
        client: MockWorkflowClient(),
        tileClient: RealTileWorkflowClient(baseURL: URL(string: "http://localhost:8000")!)
    )
}
