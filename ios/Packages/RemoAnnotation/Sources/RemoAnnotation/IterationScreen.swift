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
            // Mode picker
            Picker("Mode", selection: $mode) {
                ForEach(IterationMode.allCases, id: \.self) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .padding()

            // Design image with annotation overlay
            AnnotationCanvas(regions: $regions, maxRegions: 3)
                .aspectRatio(4/3, contentMode: .fit)
                .padding(.horizontal)

            // Iteration count
            Text("Round \(projectState.iterationCount + 1) of 5")
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.top, 8)

            Spacer()

            // Input area based on mode
            switch mode {
            case .annotation:
                annotationControls
            case .text:
                textControls
            }

            // Submit button
            Button {
                Task { await submit() }
            } label: {
                Text("Generate Revision")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!canSubmit || isSubmitting)
            .padding()

            // Approve early button
            Button("Approve This Design") {
                Task { await approve() }
            }
            .font(.subheadline)
            .padding(.bottom)
        }
        .navigationTitle("Refine Design")
        .navigationBarTitleDisplayMode(.inline)
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
            Text("Tap the image to place circles (up to 3)")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(Array(regions.enumerated()), id: \.offset) { index, region in
                RegionEditor(region: Binding(
                    get: { regions[index] },
                    set: { regions[index] = $0 }
                ), color: regionColor(for: index)) {
                    regions.remove(at: index)
                }
            }
        }
        .padding(.horizontal)
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

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // Background image placeholder
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.secondary.opacity(0.1))
                    .overlay {
                        Image(systemName: "photo.artframe")
                            .font(.largeTitle)
                            .foregroundStyle(.secondary)
                    }

                // Annotation circles
                ForEach(Array(regions.enumerated()), id: \.offset) { index, region in
                    let center = CGPoint(
                        x: region.centerX * geometry.size.width,
                        y: region.centerY * geometry.size.height
                    )
                    let radius = region.radius * min(geometry.size.width, geometry.size.height)

                    Circle()
                        .strokeBorder(regionColor(for: index), lineWidth: 3)
                        .frame(width: radius * 2, height: radius * 2)
                        .position(center)
                        .overlay {
                            Text("\(index + 1)")
                                .font(.caption.bold())
                                .foregroundStyle(.white)
                                .frame(width: 24, height: 24)
                                .background(regionColor(for: index))
                                .clipShape(Circle())
                                .position(center)
                        }
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { location in
                guard regions.count < maxRegions else { return }
                let normalizedX = location.x / geometry.size.width
                let normalizedY = location.y / geometry.size.height
                let newRegion = AnnotationRegion(
                    regionId: regions.count + 1,
                    centerX: normalizedX,
                    centerY: normalizedY,
                    radius: 0.08,
                    instruction: ""
                )
                regions.append(newRegion)
            }
        }
    }

    private func regionColor(for index: Int) -> Color {
        [Color.red, .blue, .green][index % 3]
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
