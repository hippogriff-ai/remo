import SwiftUI
import RemoModels
import RemoShoppingList

/// Final output screen: save to photos, share, view shopping list, start new project.
public struct OutputScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var showShoppingList = false
    @State private var showRevisionHistory = false
    @State private var savedToPhotos = false

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Final design image
                DesignImageView(projectState.currentImage)
                    .aspectRatio(4/3, contentMode: .fit)
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

                // Action buttons
                VStack(spacing: 12) {
                    Button {
                        saveToPhotos()
                    } label: {
                        Label(
                            savedToPhotos ? "Saved!" : "Save to Photos",
                            systemImage: savedToPhotos ? "checkmark.circle.fill" : "square.and.arrow.down"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(savedToPhotos)

                    Button {
                        showShoppingList = true
                    } label: {
                        Label("View Shopping List", systemImage: "cart")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.horizontal)
            }
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
    }

    private func saveToPhotos() {
        // In P2: save actual image via UIImageWriteToSavedPhotosAlbum
        savedToPhotos = true
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
                            systemImage: revision.type == "annotation" ? "pencil.circle" : "text.bubble"
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
