import SwiftUI
import RemoModels
import RemoNetworking

/// Swipeable design comparison: 2 options, side-by-side toggle, selection highlighting.
public struct DesignSelectionScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var selectedIndex: Int?
    @State private var showSideBySide = false
    @State private var isSelecting = false
    @State private var errorMessage: String?
    @State private var showStartOverConfirmation = false

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        VStack(spacing: 16) {
            // Toggle view mode
            Picker("View", selection: $showSideBySide) {
                Text("Swipe").tag(false)
                Text("Compare").tag(true)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)

            if showSideBySide {
                sideBySideView
            } else {
                swipeView
            }

            // Selection button
            Button {
                Task { await selectDesign() }
            } label: {
                HStack(spacing: 8) {
                    if isSelecting {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Text(isSelecting ? "Selecting..." : selectedIndex != nil ? "Choose This Design" : "Tap a design to select")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(selectedIndex == nil || isSelecting)
            .padding(.horizontal)
            .accessibilityLabel(isSelecting ? "Selecting design" : "Choose this design")
            .accessibilityIdentifier("selection_choose")

            // Start over
            Button("Start Over", role: .destructive) {
                showStartOverConfirmation = true
            }
            .font(.subheadline)
            .padding(.bottom)
            .accessibilityHint("Discards generated designs and returns to design chat")
            .accessibilityIdentifier("selection_start_over")
        }
        .navigationTitle("Choose a Design")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
        .confirmationDialog("Start Over?", isPresented: $showStartOverConfirmation, titleVisibility: .visible) {
            Button("Start Over", role: .destructive) {
                Task { await startOver() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This will discard your generated designs and return to the design chat.")
        }
    }

    @ViewBuilder
    private var swipeView: some View {
        TabView(selection: $selectedIndex) {
            ForEach(Array(projectState.generatedOptions.enumerated()), id: \.offset) { index, option in
                DesignCard(option: option, isSelected: selectedIndex == index) {
                    selectedIndex = index
                }
                .tag(Optional(index))
            }
        }
        #if os(iOS)
        .tabViewStyle(.page(indexDisplayMode: .always))
        #endif
    }

    @ViewBuilder
    private var sideBySideView: some View {
        HStack(spacing: 8) {
            ForEach(Array(projectState.generatedOptions.enumerated()), id: \.offset) { index, option in
                DesignCard(option: option, isSelected: selectedIndex == index) {
                    selectedIndex = index
                }
            }
        }
        .padding(.horizontal)
    }

    private func selectDesign() async {
        guard let index = selectedIndex, let projectId = projectState.projectId else {
            if projectState.projectId == nil {
                assertionFailure("selectDesign() called without projectId")
                errorMessage = "Project not initialized"
            }
            return
        }
        guard index < projectState.generatedOptions.count else {
            errorMessage = "Selected design is no longer available"
            selectedIndex = nil
            return
        }
        isSelecting = true
        defer { isSelecting = false }
        do {
            try await client.selectOption(projectId: projectId, index: index)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func startOver() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("startOver() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        do {
            try await client.startOver(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Design Card

struct DesignCard: View {
    let option: DesignOption
    let isSelected: Bool
    let onTap: () -> Void

    var body: some View {
        VStack(spacing: 8) {
            DesignImageView(option.imageUrl)
                .aspectRatio(4/3, contentMode: .fit)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(isSelected ? Color.accentColor : .clear, lineWidth: 3)
                )
                .overlay(alignment: .topTrailing) {
                    if isSelected {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.title2)
                            .foregroundStyle(.white, .blue)
                            .padding(8)
                            .transition(.scale.combined(with: .opacity))
                    }
                }
                .animation(.spring(response: 0.3), value: isSelected)

            Text(option.caption)
                .font(.subheadline.bold())
        }
        .onTapGesture { onTap() }
        .accessibilityIdentifier("selection_card_\(option.caption.lowercased().replacingOccurrences(of: " ", with: "_"))")
    }
}

#Preview {
    NavigationStack {
        DesignSelectionScreen(projectState: .preview(step: .selection), client: MockWorkflowClient(delay: .zero))
    }
}
