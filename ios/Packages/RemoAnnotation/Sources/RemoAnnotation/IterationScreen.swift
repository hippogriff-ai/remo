import SwiftUI
import RemoModels

/// Iteration screen: annotation-based editing + text feedback.
/// Users can place numbered circles on the design and provide instructions,
/// or just type text feedback for changes.
public struct IterationScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var mode: IterationMode = .annotation
    @State private var regions: [AnnotationRegion] = []
    @State private var textFeedback = ""
    @State private var isSubmitting = false

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
                imageURL: projectState.currentImage
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
            Text("Tap the image to place circles (up to 3). Drag to reposition.")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(regions.indices, id: \.self) { index in
                regionEditorRow(at: index)
            }
        }
        .padding(.horizontal)
    }

    @ViewBuilder
    private func regionEditorRow(at index: Int) -> some View {
        let color = regionColor(for: index)
        RegionEditor(region: $regions[index], color: color, onDelete: {
            withAnimation(.easeOut(duration: 0.2)) {
                _ = regions.remove(at: index)
            }
        })
    }

    @ViewBuilder
    private var textControls: some View {
        TextField("Describe what you'd like changed...", text: $textFeedback, axis: .vertical)
            .textFieldStyle(.roundedBorder)
            .lineLimit(2...6)
            .padding(.horizontal)
    }

    private func submit() async {
        guard let projectId = projectState.projectId else { return }
        isSubmitting = true
        defer { isSubmitting = false }

        do {
            switch mode {
            case .annotation:
                try await client.submitAnnotationEdit(projectId: projectId, annotations: regions)
            case .text:
                try await client.submitTextFeedback(projectId: projectId, feedback: textFeedback)
            }
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
            regions = []
            textFeedback = ""
        } catch {
            // TODO: error handling
        }
    }

    private func approve() async {
        guard let projectId = projectState.projectId else { return }
        do {
            try await client.approveDesign(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            // TODO: error handling
        }
    }

    private func regionColor(for index: Int) -> Color {
        [Color.red, .blue, .green][index % 3]
    }
}

// MARK: - Annotation Canvas

struct AnnotationCanvas: View {
    @Binding var regions: [AnnotationRegion]
    let maxRegions: Int
    let imageURL: String?

    @State private var draggedIndex: Int?

    private let colors: [Color] = [.red, .blue, .green]

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

                // Annotation circles with drag gesture
                ForEach(Array(regions.enumerated()), id: \.offset) { index, region in
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
                                    let nx = max(0, min(1, value.location.x / geometry.size.width))
                                    let ny = max(0, min(1, value.location.y / geometry.size.height))
                                    regions[index] = AnnotationRegion(
                                        regionId: region.regionId,
                                        centerX: nx,
                                        centerY: ny,
                                        radius: region.radius,
                                        instruction: region.instruction
                                    )
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
            }
        }
    }
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
