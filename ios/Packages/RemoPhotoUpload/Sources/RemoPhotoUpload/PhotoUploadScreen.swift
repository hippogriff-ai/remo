import SwiftUI
import PhotosUI
#if os(iOS)
import UIKit
#endif
import RemoModels
import RemoNetworking

/// Photo upload screen: camera + gallery picker, validation feedback, 2-room-photo minimum.
public struct PhotoUploadScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var selectedRoomItems: [PhotosPickerItem] = []
    @State private var selectedInspirationItems: [PhotosPickerItem] = []
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
                            PhotoThumbnail(photo: photo, onDelete: {
                                deletePhoto(photo)
                            })
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

                // Room photo buttons
                VStack(spacing: 12) {
                    #if os(iOS)
                    Button {
                        showCamera = true
                    } label: {
                        Label("Take Room Photo", systemImage: "camera")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    #endif

                    let maxRoomPhotos = max(0, 2 - projectState.roomPhotoCount)
                    if maxRoomPhotos > 0 {
                        PhotosPicker(
                            selection: $selectedRoomItems,
                            maxSelectionCount: maxRoomPhotos,
                            matching: .images
                        ) {
                            Label("Add Room Photos", systemImage: "photo.on.rectangle")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("photos_add_room")
                    }

                    // Inspiration photos (optional, max 3)
                    let maxInspirationPhotos = max(0, 3 - projectState.inspirationPhotoCount)
                    if maxInspirationPhotos > 0 {
                        PhotosPicker(
                            selection: $selectedInspirationItems,
                            maxSelectionCount: maxInspirationPhotos,
                            matching: .images
                        ) {
                            Label("Add Inspiration Photos", systemImage: "sparkles")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .tint(.purple)
                        .accessibilityIdentifier("photos_add_inspiration")
                    }
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
        .onChange(of: selectedRoomItems) { _, items in
            Task { await uploadSelectedPhotos(items, type: "room") }
        }
        .onChange(of: selectedInspirationItems) { _, items in
            Task { await uploadSelectedPhotos(items, type: "inspiration") }
        }
        #if os(iOS)
        .sheet(isPresented: $showCamera) {
            CameraView(
                onCapture: { imageData in
                    Task { await uploadPhoto(imageData, type: "room") }
                },
                onError: { message in
                    validationMessages = [message]
                }
            )
        }
        #endif
    }

    private func uploadSelectedPhotos(_ items: [PhotosPickerItem], type: String) async {
        isUploading = true
        defer {
            isUploading = false
            selectedRoomItems = []
            selectedInspirationItems = []
        }

        for item in items {
            do {
                guard let data = try await item.loadTransferable(type: Data.self) else {
                    validationMessages.append("Could not load selected photo.")
                    continue
                }
                await uploadPhoto(data, type: type)
            } catch is CancellationError {
                return
            } catch {
                validationMessages.append("Failed to load photo: \(error.localizedDescription)")
            }
        }
    }

    private func uploadPhoto(_ data: Data, type: String) async {
        guard let projectId = projectState.projectId else {
            assertionFailure("uploadPhoto() called without projectId")
            validationMessages = ["Project not initialized"]
            return
        }
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

    private func deletePhoto(_ photo: PhotoData) {
        withAnimation(.easeOut(duration: 0.2)) {
            projectState.photos.removeAll { $0.photoId == photo.photoId }
        }
        #if os(iOS)
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        #endif
    }
}

// MARK: - Photo Thumbnail

struct PhotoThumbnail: View {
    let photo: PhotoData
    var onDelete: (() -> Void)?

    var body: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(Color.secondary.opacity(0.15))
            .aspectRatio(1, contentMode: .fit)
            .overlay {
                VStack(spacing: 4) {
                    Image(systemName: photo.photoTypeEnum == .room ? "house" : "sparkles")
                        .font(.title3)
                    Text(photo.photoType.capitalized)
                        .font(.caption2)
                }
                .foregroundStyle(.secondary)
            }
            .overlay(alignment: .topTrailing) {
                if let onDelete {
                    Button {
                        onDelete()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title3)
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, .red)
                    }
                    .offset(x: 6, y: -6)
                    .accessibilityLabel("Delete \(photo.photoType) photo")
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("\(photo.photoType.capitalized) photo")
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
    var onError: ((String) -> Void)?

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onCapture: onCapture, onError: onError) }

    class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let onCapture: (Data) -> Void
        let onError: ((String) -> Void)?

        init(onCapture: @escaping (Data) -> Void, onError: ((String) -> Void)?) {
            self.onCapture = onCapture
            self.onError = onError
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            picker.dismiss(animated: true)
            guard let image = info[.originalImage] as? UIImage else {
                onError?("Could not read the captured photo.")
                return
            }
            guard let data = image.jpegData(compressionQuality: 0.85) else {
                onError?("Failed to convert photo to JPEG format.")
                return
            }
            onCapture(data)
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            picker.dismiss(animated: true)
        }
    }
}
#endif
