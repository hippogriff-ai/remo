import SwiftUI
import PhotosUI
import RemoModels
import RemoNetworking

/// Photo upload screen: camera + gallery picker, validation feedback, 2-room-photo minimum.
public struct PhotoUploadScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var selectedItems: [PhotosPickerItem] = []
    @State private var isUploading = false
    @State private var validationMessages: [String] = []
    @State private var showCamera = false

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    public var body: some View {
        ScrollView {
            VStack(spacing: 24) {
                // Header
                VStack(spacing: 8) {
                    Image(systemName: "camera.fill")
                        .font(.system(size: 48))
                        .foregroundStyle(.tint)
                    Text("Upload Room Photos")
                        .font(.title2.bold())
                    Text("Take at least 2 photos of your room.\nOptionally add up to 3 inspiration photos.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .padding(.top)

                // Photo grid
                if !projectState.photos.isEmpty {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 100))], spacing: 12) {
                        ForEach(projectState.photos) { photo in
                            PhotoThumbnail(photo: photo)
                        }
                    }
                    .padding(.horizontal)
                }

                // Status
                HStack {
                    Label("\(projectState.roomPhotoCount)/2 room photos", systemImage: "house")
                        .foregroundStyle(projectState.roomPhotoCount >= 2 ? .green : .primary)
                    Spacer()
                    Label("\(projectState.inspirationPhotoCount)/3 inspiration", systemImage: "sparkles")
                        .foregroundStyle(.secondary)
                }
                .font(.footnote)
                .padding(.horizontal)

                // Validation messages
                ForEach(validationMessages, id: \.self) { message in
                    Label(message, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .padding(.horizontal)
                }

                // Upload buttons
                VStack(spacing: 12) {
                    Button {
                        showCamera = true
                    } label: {
                        Label("Take Photo", systemImage: "camera")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)

                    PhotosPicker(
                        selection: $selectedItems,
                        maxSelectionCount: 5 - projectState.photos.count,
                        matching: .images
                    ) {
                        Label("Choose from Library", systemImage: "photo.on.rectangle")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.horizontal)
                .disabled(isUploading)

                if isUploading {
                    ProgressView("Uploading...")
                }

                Spacer(minLength: 40)
            }
        }
        .navigationTitle("Photos")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .onChange(of: selectedItems) { _, items in
            Task { await uploadSelectedPhotos(items) }
        }
        #if os(iOS)
        .sheet(isPresented: $showCamera) {
            CameraView { imageData in
                Task { await uploadPhoto(imageData, type: "room") }
            }
        }
        #endif
    }

    private func uploadSelectedPhotos(_ items: [PhotosPickerItem]) async {
        isUploading = true
        defer { isUploading = false; selectedItems = [] }

        for item in items {
            guard let data = try? await item.loadTransferable(type: Data.self) else { continue }
            await uploadPhoto(data, type: "room")
        }
    }

    private func uploadPhoto(_ data: Data, type: String) async {
        guard let projectId = projectState.projectId else { return }
        do {
            let response = try await client.uploadPhoto(projectId: projectId, imageData: data, photoType: type)
            if !response.validation.passed {
                validationMessages = response.validation.messages
            } else {
                validationMessages = []
                // Refresh state to pick up new photos and possible step transition
                let newState = try await client.getState(projectId: projectId)
                projectState.apply(newState)
            }
        } catch {
            validationMessages = [error.localizedDescription]
        }
    }
}

// MARK: - Photo Thumbnail

struct PhotoThumbnail: View {
    let photo: PhotoData

    var body: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Color.secondary.opacity(0.15))
            .aspectRatio(1, contentMode: .fit)
            .overlay {
                VStack(spacing: 4) {
                    Image(systemName: photo.photoType == "room" ? "house" : "sparkles")
                        .font(.title3)
                    Text(photo.photoType.capitalized)
                        .font(.caption2)
                }
                .foregroundStyle(.secondary)
            }
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        PhotoUploadScreen(projectState: .preview(step: .photoUpload), client: MockWorkflowClient(delay: .zero))
    }
}

#if os(iOS)
// MARK: - Camera View (UIImagePickerController bridge)

struct CameraView: UIViewControllerRepresentable {
    let onCapture: (Data) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onCapture: onCapture) }

    class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let onCapture: (Data) -> Void

        init(onCapture: @escaping (Data) -> Void) {
            self.onCapture = onCapture
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            picker.dismiss(animated: true)
            guard let image = info[.originalImage] as? UIImage,
                  let data = image.jpegData(compressionQuality: 0.85) else { return }
            onCapture(data)
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            picker.dismiss(animated: true)
        }
    }
}
#endif
