import SwiftUI
#if os(iOS)
import UIKit
import Photos
#endif
import RemoModels
import RemoNetworking
import RemoShoppingList

/// Final output screen: save to photos, share, view shopping list, start new project.
public struct OutputScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var showShoppingList = false
    @State private var showRevisionHistory = false
    @State private var savedToPhotos = false
    @State private var isSaving = false
    @State private var saveError: String?
    @State private var zoomScale: CGFloat = 1.0

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        VStack(spacing: 0) {
            // Scrollable content area
            ScrollView {
                VStack(spacing: 20) {
                    // Final design image (pinch to zoom)
                    DesignImageView(projectState.currentImage)
                        .aspectRatio(4/3, contentMode: .fit)
                        .scaleEffect(zoomScale)
                        .gesture(
                            MagnifyGesture()
                                .onChanged { value in
                                    zoomScale = max(1.0, min(3.0, value.magnification))
                                }
                                .onEnded { _ in
                                    withAnimation(.spring(response: 0.3)) {
                                        zoomScale = 1.0
                                    }
                                }
                        )
                        .padding(.horizontal)

                    Text("Your Design is Ready!")
                        .font(.title2.bold())

                    if projectState.iterationCount > 0 {
                        Button {
                            showRevisionHistory = true
                        } label: {
                            Label("\(projectState.iterationCount) revision\(projectState.iterationCount == 1 ? "" : "s")", systemImage: "clock.arrow.circlepath")
                                .font(.caption)
                        }
                    }

                    Text("Save your design image and copy your specs.\nProject data will be deleted after 24 hours.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal)
                }
                .padding(.vertical)
            }

            Divider()

            // Action buttons — fixed at bottom so they're always reachable
            VStack(spacing: 12) {
                Button {
                    Task { await saveToPhotos() }
                } label: {
                    Label(
                        savedToPhotos ? "Saved!" : (isSaving ? "Saving..." : "Save to Photos"),
                        systemImage: savedToPhotos ? "checkmark.circle.fill" : "square.and.arrow.down"
                    )
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(savedToPhotos || isSaving)
                .accessibilityLabel(savedToPhotos ? "Design saved to Photos" : "Save to Photos")
                .accessibilityIdentifier("output_save")

                if let imageUrl = projectState.currentImage, let url = URL(string: imageUrl) {
                    ShareLink(item: url) {
                        Label("Share Design", systemImage: "square.and.arrow.up")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityLabel("Share design image")
                    .accessibilityIdentifier("output_share")
                }

                Button {
                    showShoppingList = true
                } label: {
                    Label("View Shopping List", systemImage: "cart")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("output_shopping")
            }
            .padding()
        }
        .navigationTitle("Complete")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .sheet(isPresented: $showShoppingList) {
            NavigationStack {
                ShoppingListScreen(projectState: projectState)
            }
        }
        .sheet(isPresented: $showRevisionHistory) {
            NavigationStack {
                RevisionHistoryView(revisions: projectState.revisionHistory)
            }
        }
        .alert("Save Error", isPresented: .init(get: { saveError != nil }, set: { if !$0 { saveError = nil } })) {
            Button("OK") { saveError = nil }
        } message: {
            Text(saveError ?? "")
        }
    }

    private func saveToPhotos() async {
        guard let imageUrlString = projectState.currentImage,
              let url = URL(string: imageUrlString) else {
            saveError = "No image available to save"
            return
        }
        isSaving = true
        defer { isSaving = false }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            #if os(iOS)
            guard let image = UIImage(data: data) else {
                saveError = "Could not decode image"
                return
            }
            try await PHPhotoLibrary.shared().performChanges {
                PHAssetChangeRequest.creationRequestForAsset(from: image)
            }
            #endif
            savedToPhotos = true
        } catch {
            saveError = "Failed to save: \(error.localizedDescription)"
        }
    }
}

// MARK: - Revision History

struct RevisionHistoryView: View {
    let revisions: [RevisionRecord]

    var body: some View {
        List {
            ForEach(revisions, id: \.revisionNumber) { revision in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Label(
                            "Revision \(revision.revisionNumber)",
                            systemImage: revision.revisionTypeEnum == .annotation ? "pencil.circle" : "text.bubble"
                        )
                        .font(.subheadline.bold())

                        Spacer()

                        Text(revision.type.capitalized)
                            .font(.caption)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(Color.secondary.opacity(0.15))
                            .clipShape(Capsule())
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
    }
}

#Preview {
    NavigationStack {
        OutputScreen(projectState: .preview(step: .completed), client: MockWorkflowClient(delay: .zero))
    }
}
