import SwiftUI
#if os(iOS)
import UIKit
#endif
import RemoModels
import RemoNetworking

/// Iteration screen: annotation-based editing + text feedback.
/// Users can place numbered circles on the design and provide instructions,
/// or just type text feedback for changes.
public struct IterationScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var mode: IterationMode = .annotation
    @State private var regions: [AnnotationRegion] = []
    @State private var regionHistory: [[AnnotationRegion]] = []
    @State private var textFeedback = ""
    @State private var isSubmitting = false
    @State private var isApproving = false
    @State private var errorMessage: String?
    @State private var showOverlapWarning = false
    @State private var showRevisionHistory = false
    @State private var showApprovalConfirmation = false
    @State private var showRegionPanel = false
    @State private var highlightedRegionId: Int?

    enum IterationMode: String, CaseIterable {
        case annotation = "Mark Areas"
        case text = "Text Feedback"
    }

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        VStack(spacing: 0) {
            Picker("Mode", selection: $mode) {
                ForEach(IterationMode.allCases, id: \.self) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .padding()

            AnnotationCanvas(
                regions: $regions,
                maxRegions: 3,
                imageURL: projectState.currentImage,
                onWillMutate: { snapshotRegions() },
                onOverlap: { showOverlapWarning = true },
                highlightedRegionId: $highlightedRegionId
            )
            .aspectRatio(4/3, contentMode: .fit)
            .padding(.horizontal)

            HStack {
                Text("Round \(projectState.iterationCount + 1) of 5")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if projectState.iterationCount > 0 {
                    Button {
                        showRevisionHistory = true
                    } label: {
                        Label("History", systemImage: "clock.arrow.circlepath")
                            .font(.caption)
                    }
                }
            }
            .padding(.top, 8)

            if projectState.iterationCount >= 5 {
                Text("You've used all 5 revision rounds. Please approve your design or start a new project.")
                    .font(.subheadline)
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
                    .padding(.top, 8)
                    .accessibilityIdentifier("iteration_limit_message")
            }

            Spacer()

            if projectState.iterationCount < 5 {
                switch mode {
                case .annotation:
                    annotationControls
                case .text:
                    textControls
                }

                Button {
                    Task { await submit() }
                } label: {
                    HStack {
                        if isSubmitting {
                            ProgressView()
                                .tint(.white)
                        }
                        Text(isSubmitting ? "Generating..." : "Generate Revision")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(!canSubmit || isSubmitting || isApproving)
                .padding()
                .accessibilityLabel(isSubmitting ? "Generating revision" : "Generate Revision")
                .accessibilityHint("Sends your edits to generate a revised design")
                .accessibilityIdentifier("iteration_submit")
            }

            Button("Approve This Design") {
                showApprovalConfirmation = true
            }
            .font(.subheadline)
            .disabled(isSubmitting || isApproving)
            .padding(.bottom)
            .accessibilityHint("Approve the current design and continue to shopping list")
            .accessibilityIdentifier("iteration_approve")
        }
        .navigationTitle("Refine Design")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
        .confirmationDialog("Approve Design?", isPresented: $showApprovalConfirmation, titleVisibility: .visible) {
            Button("Approve") {
                Task { await approve() }
            }
            Button("Keep Editing", role: .cancel) {}
        } message: {
            Text("Happy with this design? Once approved, it's final.")
        }
        .alert("Regions Can't Overlap", isPresented: $showOverlapWarning) {
            Button("OK") {}
        } message: {
            Text("Please draw around a different area, or delete an existing region first.")
        }
        .sheet(isPresented: $showRevisionHistory) {
            NavigationStack {
                List {
                    ForEach(projectState.revisionHistory, id: \.revisionNumber) { revision in
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Revision \(revision.revisionNumber)")
                                .font(.subheadline.bold())

                            if let url = URL(string: revision.revisedImageUrl) {
                                AsyncImage(url: url) { phase in
                                    switch phase {
                                    case .success(let image):
                                        image
                                            .resizable()
                                            .aspectRatio(contentMode: .fit)
                                            .clipShape(RoundedRectangle(cornerRadius: 8))
                                    case .failure:
                                        Label("Image unavailable", systemImage: "photo.badge.exclamationmark")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .frame(maxWidth: .infinity)
                                            .frame(height: 120)
                                    default:
                                        ProgressView()
                                            .frame(maxWidth: .infinity)
                                            .frame(height: 120)
                                    }
                                }
                                .frame(maxHeight: 200)
                                .accessibilityIdentifier("revision_image_\(revision.revisionNumber)")
                            }

                            ForEach(revision.instructions, id: \.self) { instruction in
                                Text(instruction)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
                .navigationTitle("Revision History")
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Done") { showRevisionHistory = false }
                    }
                }
            }
        }
        .sheet(isPresented: $showRegionPanel) {
            NavigationStack {
                RegionListPanel(regions: $regions, onDelete: { index in
                    snapshotRegions()
                    guard index < regions.count else { return }
                    withAnimation(.easeOut(duration: 0.2)) {
                        regions.remove(at: index)
                        // Renumber remaining regions to maintain 1..N (LASSO-11)
                        for i in regions.indices {
                            regions[i].regionId = i + 1
                        }
                    }
                    #if os(iOS)
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    #endif
                }, onHighlight: { regionId in
                    highlightedRegionId = regionId
                })
                .navigationTitle("Edit Regions")
                #if os(iOS)
                .navigationBarTitleDisplayMode(.inline)
                #endif
                .toolbar {
                    #if os(iOS)
                    ToolbarItem(placement: .cancellationAction) {
                        EditButton()
                            .accessibilityIdentifier("region_list_edit_button")
                    }
                    #endif
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") {
                            showRegionPanel = false
                            highlightedRegionId = nil
                        }
                    }
                }
            }
            .presentationDetents([.medium, .large])
            .presentationDragIndicator(.visible)
            .presentationBackgroundInteraction(.enabled(upThrough: .medium))
        }
        .onChange(of: regions.count) { oldCount, newCount in
            if newCount > oldCount && !showRegionPanel && mode == .annotation {
                showRegionPanel = true
            }
        }
        .onChange(of: showRegionPanel) { _, isShowing in
            if !isShowing {
                highlightedRegionId = nil
            }
        }
    }

    private var canSubmit: Bool {
        switch mode {
        case .annotation:
            return regions.contains { $0.instruction.count >= 10 }
        case .text:
            return textFeedback.trimmingCharacters(in: .whitespaces).count >= 10
        }
    }

    @ViewBuilder
    private var annotationControls: some View {
        VStack(spacing: 8) {
            HStack {
                Text("Tap the image to place circles (up to 3). Drag to reposition.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if !regionHistory.isEmpty {
                    Button {
                        undoLastAction()
                    } label: {
                        Label("Undo", systemImage: "arrow.uturn.backward")
                            .font(.caption)
                    }
                }
            }

            if !regions.isEmpty {
                Button {
                    showRegionPanel = true
                } label: {
                    HStack(spacing: 8) {
                        ForEach(Array(regions.enumerated()), id: \.element.regionId) { index, region in
                            HStack(spacing: 4) {
                                Circle()
                                    .fill(regionColor(for: index))
                                    .frame(width: 20, height: 20)
                                    .overlay {
                                        Text("\(index + 1)")
                                            .font(.caption2.bold())
                                            .foregroundStyle(.white)
                                    }
                                Text(region.action ?? "Replace")
                                    .font(.caption2)
                                    .lineLimit(1)
                            }
                        }

                        Spacer()

                        Label("Edit", systemImage: "pencil.circle")
                            .font(.caption)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(Color.secondary.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("region_edit_panel_button")
            }
        }
        .padding(.horizontal)
    }


    @ViewBuilder
    private var textControls: some View {
        VStack(alignment: .leading, spacing: 4) {
            TextField("Describe what you'd like changed...", text: $textFeedback, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...6)
                .accessibilityIdentifier("iteration_text_input")

            let trimmedCount = textFeedback.trimmingCharacters(in: .whitespaces).count
            if !textFeedback.isEmpty && trimmedCount < 10 {
                Text("Please provide more detail (at least 10 characters)")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.horizontal)
    }

    private func submit() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("submit() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        isSubmitting = true
        defer { isSubmitting = false }

        let previousCount = projectState.iterationCount

        do {
            switch mode {
            case .annotation:
                let validRegions = regions.filter { $0.instruction.count >= 10 }
                guard !validRegions.isEmpty else { return }
                try await client.submitAnnotationEdit(projectId: projectId, annotations: validRegions)
            case .text:
                let trimmed = textFeedback.trimmingCharacters(in: .whitespaces)
                guard !trimmed.isEmpty else { return }
                try await client.submitTextFeedback(projectId: projectId, feedback: trimmed)
            }
            // Poll until iteration_count increases or an error appears.
            // With mock backend this returns immediately; with real Temporal + Gemini
            // the edit activity takes 15-30s.
            let poller = PollingManager(client: client)
            let newState = try await poller.pollUntil(projectId: projectId) { state in
                state.iterationCount > previousCount
            }
            projectState.apply(newState)
            // pollUntil returns on EITHER condition met OR error — surface backend errors
            if let workflowError = newState.error {
                errorMessage = workflowError.message
                return
            }
            regions = []
            regionHistory = []
            textFeedback = ""
        } catch is CancellationError {
            // View disappeared while polling — do nothing
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func approve() async {
        guard !isApproving else { return }
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

    private func snapshotRegions() {
        regionHistory.append(regions)
    }

    private func undoLastAction() {
        guard let previous = regionHistory.popLast() else { return }
        withAnimation(.easeInOut(duration: 0.2)) {
            regions = previous
        }
    }

    private func regionColor(for index: Int) -> Color {
        [Color.red, .blue, .green][index % 3]
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        IterationScreen(projectState: .preview(step: .iteration), client: MockWorkflowClient(delay: .zero))
    }
}

// MARK: - Annotation Canvas

struct AnnotationCanvas: View {
    @Binding var regions: [AnnotationRegion]
    let maxRegions: Int
    let imageURL: String?
    var onWillMutate: (() -> Void)?
    var onOverlap: (() -> Void)?
    @Binding var highlightedRegionId: Int?

    @State private var isDragging = false
    @State private var activeGuides: SnapGuides = SnapGuides()
    @State private var pinchBaseRadius: Double?

    private let colors: [Color] = [.red, .blue, .green]
    /// Snap threshold in normalized coordinates (2% of canvas dimension)
    private let snapThreshold: Double = 0.02
    private let minRadius: Double = 0.04
    private let maxRadius: Double = 0.20

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // Background image
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.secondary.opacity(0.1))
                    .overlay {
                        if let imageURL, let url = URL(string: imageURL) {
                            AsyncImage(url: url) { phase in
                                switch phase {
                                case .success(let image):
                                    image.resizable().scaledToFill()
                                default:
                                    Image(systemName: "photo.artframe")
                                        .font(.largeTitle)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        } else {
                            Image(systemName: "photo.artframe")
                                .font(.largeTitle)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 12))

                // Snap guide lines (drawn behind circles)
                if isDragging {
                    snapGuideOverlay(in: geometry.size)
                }

                // Annotation circles with drag gesture
                ForEach(Array(regions.enumerated()), id: \.element.regionId) { index, region in
                    let color = colors[index % 3]
                    let minDim = min(geometry.size.width, geometry.size.height)
                    let radius = region.radius * minDim

                    let isHighlighted = highlightedRegionId == region.regionId

                    Circle()
                        .strokeBorder(color, lineWidth: isHighlighted ? 5 : 3)
                        .background(Circle().fill(color.opacity(isHighlighted ? 0.3 : 0.15)))
                        .frame(width: radius * 2, height: radius * 2)
                        .shadow(color: isHighlighted ? color.opacity(0.6) : .clear, radius: 8)
                        .animation(.easeInOut(duration: 0.3), value: highlightedRegionId)
                        .overlay {
                            let chipSize: CGFloat = 24
                            let halfChip = chipSize / 2
                            let cx = region.centerX * geometry.size.width
                            let cy = region.centerY * geometry.size.height
                            let clampedX = min(max(cx, halfChip), geometry.size.width - halfChip) - cx
                            let clampedY = min(max(cy, halfChip), geometry.size.height - halfChip) - cy
                            Text("\(index + 1)")
                                .font(.caption.bold())
                                .foregroundStyle(.white)
                                .frame(width: chipSize, height: chipSize)
                                .background(color)
                                .clipShape(Circle())
                                .offset(x: clampedX, y: clampedY)
                        }
                        .position(
                            x: region.centerX * geometry.size.width,
                            y: region.centerY * geometry.size.height
                        )
                        .gesture(
                            DragGesture()
                                .onChanged { value in
                                    if !isDragging {
                                        isDragging = true
                                        onWillMutate?()
                                    }
                                    guard let currentIndex = regions.firstIndex(where: { $0.regionId == region.regionId }) else { return }
                                    var nx = max(0, min(1, value.location.x / geometry.size.width))
                                    var ny = max(0, min(1, value.location.y / geometry.size.height))
                                    let guides = RemoAnnotation.computeSnapGuides(x: nx, y: ny, excludingRegionId: region.regionId, regions: regions, threshold: snapThreshold)
                                    activeGuides = guides
                                    if guides.snapX { nx = guides.snappedX ?? nx }
                                    if guides.snapY { ny = guides.snappedY ?? ny }
                                    var updated = region
                                    updated.centerX = nx
                                    updated.centerY = ny
                                    regions[currentIndex] = updated
                                }
                                .onEnded { _ in
                                    isDragging = false
                                    activeGuides = SnapGuides()
                                }
                        )
                        .simultaneousGesture(
                            MagnifyGesture()
                                .onChanged { value in
                                    guard let currentIndex = regions.firstIndex(where: { $0.regionId == region.regionId }) else { return }
                                    if pinchBaseRadius == nil {
                                        onWillMutate?()
                                        pinchBaseRadius = region.radius
                                    }
                                    let base = pinchBaseRadius ?? region.radius
                                    let newRadius = min(maxRadius, max(minRadius, base * value.magnification))
                                    var updated = region
                                    updated.radius = newRadius
                                    regions[currentIndex] = updated
                                }
                                .onEnded { _ in
                                    pinchBaseRadius = nil
                                }
                        )
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .contentShape(Rectangle())
            .onTapGesture(coordinateSpace: .local) { (location: CGPoint) in
                guard regions.count < maxRegions else { return }
                let nx = location.x / geometry.size.width
                let ny = location.y / geometry.size.height
                let newRadius = 0.08
                let overlaps = RemoAnnotation.checkRegionOverlap(
                    x: nx, y: ny, radius: newRadius, existingRegions: regions
                )
                if overlaps {
                    onOverlap?()
                    return
                }
                onWillMutate?()
                let nextId = (regions.map(\.regionId).max() ?? 0) + 1
                let newRegion = AnnotationRegion(
                    regionId: nextId,
                    centerX: nx,
                    centerY: ny,
                    radius: newRadius,
                    instruction: ""
                )
                withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                    regions.append(newRegion)
                }
                #if os(iOS)
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                #endif
            }
        }
    }

    // MARK: - Snap Guide Overlay

    @ViewBuilder
    private func snapGuideOverlay(in size: CGSize) -> some View {
        ForEach(activeGuides.verticalLines, id: \.self) { nx in
            Path { path in
                let x = nx * size.width
                path.move(to: CGPoint(x: x, y: 0))
                path.addLine(to: CGPoint(x: x, y: size.height))
            }
            .stroke(style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
            .foregroundStyle(.yellow.opacity(0.7))
        }
        ForEach(activeGuides.horizontalLines, id: \.self) { ny in
            Path { path in
                let y = ny * size.height
                path.move(to: CGPoint(x: 0, y: y))
                path.addLine(to: CGPoint(x: size.width, y: y))
            }
            .stroke(style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
            .foregroundStyle(.yellow.opacity(0.7))
        }
    }
}

/// Tracks which snap guide lines are active during a drag.
struct SnapGuides {
    var verticalLines: [Double] = []
    var horizontalLines: [Double] = []
    var snapX = false
    var snapY = false
    var snappedX: Double?
    var snappedY: Double?
}

/// Pure function for snap guide computation — extracted for testability.
func computeSnapGuides(
    x: Double,
    y: Double,
    excludingRegionId: Int,
    regions: [AnnotationRegion],
    threshold: Double = 0.02
) -> SnapGuides {
    var guides = SnapGuides()

    // Snap to canvas center (0.5, 0.5)
    if abs(x - 0.5) < threshold {
        guides.verticalLines.append(0.5)
        guides.snapX = true
        guides.snappedX = 0.5
    }
    if abs(y - 0.5) < threshold {
        guides.horizontalLines.append(0.5)
        guides.snapY = true
        guides.snappedY = 0.5
    }

    // Snap to other regions' centers
    for other in regions where other.regionId != excludingRegionId {
        if abs(x - other.centerX) < threshold && !guides.snapX {
            guides.verticalLines.append(other.centerX)
            guides.snapX = true
            guides.snappedX = other.centerX
        }
        if abs(y - other.centerY) < threshold && !guides.snapY {
            guides.horizontalLines.append(other.centerY)
            guides.snapY = true
            guides.snappedY = other.centerY
        }
    }

    return guides
}

/// Check if a proposed region at (x, y) with given radius overlaps any existing region.
/// Extracted for testability — used by AnnotationCanvas tap gesture.
func checkRegionOverlap(
    x: Double,
    y: Double,
    radius: Double,
    existingRegions: [AnnotationRegion]
) -> Bool {
    existingRegions.contains { existing in
        let dx = x - existing.centerX
        let dy = y - existing.centerY
        let distance = (dx * dx + dy * dy).squareRoot()
        return distance < (radius + existing.radius)
    }
}

// MARK: - Region Actions

private let regionActions = ["Replace", "Remove", "Change finish", "Resize", "Reposition"]

// MARK: - Region List Panel

struct RegionListPanel: View {
    @Binding var regions: [AnnotationRegion]
    let onDelete: (Int) -> Void
    var onHighlight: ((Int?) -> Void)?

    @State private var expandedRegionId: Int?

    private let colors: [Color] = [.red, .blue, .green]

    var body: some View {
        List {
            ForEach(Array(regions.enumerated()), id: \.element.regionId) { index, region in
                RegionListRow(
                    region: $regions[index],
                    displayNumber: index + 1,
                    color: colors[index % 3],
                    isExpanded: expandedRegionId == region.regionId,
                    onTap: {
                        withAnimation {
                            let wasExpanded = expandedRegionId == region.regionId
                            expandedRegionId = wasExpanded ? nil : region.regionId
                            onHighlight?(wasExpanded ? nil : region.regionId)
                        }
                    }
                )
            }
            .onDelete { indexSet in
                for index in indexSet.sorted(by: >) {
                    onDelete(index)
                }
            }
            .onMove { source, destination in
                regions.move(fromOffsets: source, toOffset: destination)
                renumberRegions()
            }

            if regions.count < 3 {
                Text("Tap the image to add more regions")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .listRowBackground(Color.clear)
            }
        }
        #if os(iOS)
        .listStyle(.insetGrouped)
        #endif
        .onChange(of: regions.count) { _, _ in
            if let lastRegion = regions.last {
                expandedRegionId = lastRegion.regionId
            }
        }
    }

    private func renumberRegions() {
        for i in regions.indices {
            regions[i].regionId = i + 1
        }
    }
}

struct RegionListRow: View {
    @Binding var region: AnnotationRegion
    let displayNumber: Int
    let color: Color
    let isExpanded: Bool
    let onTap: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: onTap) {
                HStack(spacing: 10) {
                    Circle()
                        .fill(color)
                        .frame(width: 28, height: 28)
                        .overlay {
                            Text("\(displayNumber)")
                                .font(.caption.bold())
                                .foregroundStyle(.white)
                        }

                    VStack(alignment: .leading, spacing: 2) {
                        Text(region.action ?? "Replace")
                            .font(.subheadline.bold())

                        if !region.instruction.isEmpty {
                            Text(String(region.instruction.prefix(40)) + (region.instruction.count > 40 ? "\u{2026}" : ""))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        } else {
                            Text("Tap to add instruction")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                    }

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)

            if isExpanded {
                Divider()

                Picker("Action", selection: Binding(
                    get: { region.action ?? "Replace" },
                    set: { region.action = $0 }
                )) {
                    ForEach(regionActions, id: \.self) { action in
                        Text(action).tag(action)
                    }
                }
                .pickerStyle(.menu)

                TextField("Instruction (min 10 chars)", text: $region.instruction)
                    .textFieldStyle(.roundedBorder)
                    .font(.subheadline)

                TextField("Avoid (comma-separated)", text: avoidBinding)
                    .textFieldStyle(.roundedBorder)
                    .font(.subheadline)
                    .accessibilityIdentifier("region_avoid_\(displayNumber)")

                Text("Style nudges")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                FlowLayout(spacing: 6) {
                    ForEach(styleNudges, id: \.self) { nudge in
                        let isOn = region.constraints.contains(nudge)
                        Button {
                            if isOn {
                                region.constraints.removeAll { $0 == nudge }
                            } else {
                                region.constraints.append(nudge)
                            }
                        } label: {
                            Text(nudge)
                                .font(.caption2)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 5)
                                .background(isOn ? Color.accentColor.opacity(0.2) : Color.secondary.opacity(0.1))
                                .foregroundStyle(isOn ? .primary : .secondary)
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("nudge_\(nudge.replacingOccurrences(of: " ", with: "_"))")
                    }
                }
            }
        }
        .padding(.vertical, 4)
        .accessibilityIdentifier("region_list_row_\(displayNumber)")
    }

    private var avoidBinding: Binding<String> {
        Binding(
            get: { region.avoid.joined(separator: ", ") },
            set: { newValue in
                region.avoid = newValue
                    .split(separator: ",")
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
            }
        )
    }
}

private let styleNudges = [
    "cheaper", "premium", "more minimal", "more cozy",
    "more modern", "pet-friendly", "kid-friendly", "low maintenance",
]

/// Simple flow layout that wraps items to next line when they don't fit.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let result = layout(in: proposal.width ?? .infinity, subviews: subviews)
        return result.size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = layout(in: bounds.width, subviews: subviews)
        for (index, position) in result.positions.enumerated() {
            subviews[index].place(at: CGPoint(x: bounds.minX + position.x, y: bounds.minY + position.y), proposal: .unspecified)
        }
    }

    private func layout(in width: CGFloat, subviews: Subviews) -> (size: CGSize, positions: [CGPoint]) {
        var positions: [CGPoint] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var maxWidth: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > width, x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            positions.append(CGPoint(x: x, y: y))
            rowHeight = max(rowHeight, size.height)
            x += size.width + spacing
            maxWidth = max(maxWidth, x - spacing)
        }

        return (CGSize(width: maxWidth, height: y + rowHeight), positions)
    }
}
