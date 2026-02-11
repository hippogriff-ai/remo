import SwiftUI
import RemoModels

/// Final output screen: save to photos, share, view shopping list, start new project.
public struct OutputScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var showShoppingList = false
    @State private var showShareSheet = false
    @State private var savedToPhotos = false

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Final design image
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color.secondary.opacity(0.1))
                    .aspectRatio(4/3, contentMode: .fit)
                    .overlay {
                        VStack {
                            Image(systemName: "sparkles")
                                .font(.system(size: 40))
                                .foregroundStyle(.yellow)
                            Text("Your Redesigned Room")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.horizontal)

                Text("Your Design is Ready!")
                    .font(.title2.bold())

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
                        showShareSheet = true
                    } label: {
                        Label("Share", systemImage: "square.and.arrow.up")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)

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
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showShoppingList) {
            NavigationStack {
                ShoppingListScreen(projectState: projectState)
            }
        }
    }

    private func saveToPhotos() {
        // In P2: save actual image to camera roll via UIImageWriteToSavedPhotosAlbum
        savedToPhotos = true
    }
}
