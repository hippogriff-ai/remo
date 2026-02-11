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
    @State private var errorMessage: String?

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
                onWillMutate: { snapshotRegions() }
            )
            .aspectRatio(4/3, contentMode: .fit)
            .padding(.horizontal)

            Text("Round \(projectState.iterationCount + 1) of 5")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.top, 8)

            Spacer()

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
            .disabled(!canSubmit || isSubmitting)
            .padding()

            Button("Approve This Design") {
                Task { await approve() }
            }
            .font(.subheadline)
            .padding(.bottom)
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
    }

    private var canSubmit: Bool {
        switch mode {
        case .annotation:
            return !regions.isEmpty && regions.allSatisfy { $0.instruction.count >= 10 }
        case .text:
            return !textFeedback.trimmingCharacters(in: .whitespaces).isEmpty
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

            ForEach(regions.indices, id: \.self) { index in
                regionEditorRow(at: index)
            }
        }
        .padding(.horizontal)
    }

    @ViewBuilder
    private func regionEditorRow(at index: Int) -> some View {
        if index < regions.count {
            let color = regionColor(for: index)
            RegionEditor(region: $regions[index], color: color, onDelete: {
                snapshotRegions()
                withAnimation(.easeOut(duration: 0.2)) {
                    guard index < regions.count else { return }
                    regions.remove(at: index)
                }
                #if os(iOS)
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                #endif
            })
        }
    }

    @ViewBuilder
    private var textControls: some View {
        TextField("Describe what you'd like changed...", text: $textFeedback, axis: .vertical)
            .textFieldStyle(.roundedBorder)
            .lineLimit(2...6)
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
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
            regions = []
            regionHistory = []
            textFeedback = ""
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func approve() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("approve() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
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

                    Circle()
                        .strokeBorder(color, lineWidth: 3)
                        .background(Circle().fill(color.opacity(0.15)))
                        .frame(width: radius * 2, height: radius * 2)
                        .overlay {
                            Text("\(index + 1)")
                                .font(.caption.bold())
                                .foregroundStyle(.white)
                                .frame(width: 24, height: 24)
                                .background(color)
                                .clipShape(Circle())
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
                                    regions[currentIndex] = AnnotationRegion(
                                        regionId: region.regionId,
                                        centerX: nx,
                                        centerY: ny,
                                        radius: region.radius,
                                        instruction: region.instruction
                                    )
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
                                    regions[currentIndex] = AnnotationRegion(
                                        regionId: region.regionId,
                                        centerX: region.centerX,
                                        centerY: region.centerY,
                                        radius: newRadius,
                                        instruction: region.instruction
                                    )
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
                onWillMutate?()
                let nx = location.x / geometry.size.width
                let ny = location.y / geometry.size.height
                let newRegion = AnnotationRegion(
                    regionId: regions.count + 1,
                    centerX: nx,
                    centerY: ny,
                    radius: 0.08,
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

// MARK: - Region Editor

struct RegionEditor: View {
    @Binding var region: AnnotationRegion
    let color: Color
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(color)
                .frame(width: 28, height: 28)
                .overlay {
                    Text("\(region.regionId)")
                        .font(.caption.bold())
                        .foregroundStyle(.white)
                }

            TextField("Instruction (min 10 chars)", text: $region.instruction)
                .textFieldStyle(.roundedBorder)
                .font(.subheadline)

            Button(role: .destructive) {
                onDelete()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
        }
    }
}
