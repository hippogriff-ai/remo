import SwiftUI
import RemoModels

/// LiDAR scan screen: device capability check, scan flow, skip option.
public struct LiDARScanScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var isScanning = false
    @State private var showSkipConfirmation = false

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    // Simplified LiDAR check — full check uses ARWorldTrackingConfiguration
    private var hasLiDAR: Bool {
        // In P2: check ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
        // For now, always show the option (mock will succeed)
        true
    }

    public var body: some View {
        VStack(spacing: 24) {
            Spacer()

            Image(systemName: "cube.transparent")
                .font(.system(size: 64))
                .foregroundStyle(.tint)

            Text("Scan Your Room")
                .font(.title2.bold())

            Text("Use LiDAR to capture room dimensions.\nThis helps find furniture that fits your space.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()

            if hasLiDAR {
                Button {
                    Task { await startScan() }
                } label: {
                    Label("Start Scanning", systemImage: "viewfinder")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(isScanning)
            } else {
                Text("LiDAR is not available on this device.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            Button("Skip Scan") {
                showSkipConfirmation = true
            }
            .font(.subheadline)
            .padding(.bottom)
        }
        .padding()
        .navigationTitle("Room Scan")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .alert("Skip Room Scan?", isPresented: $showSkipConfirmation) {
            Button("Skip", role: .destructive) {
                Task { await skipScan() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Without a scan, furniture sizing won't be available in your shopping list.")
        }
    }

    private func startScan() async {
        guard let projectId = projectState.projectId else { return }
        isScanning = true
        defer { isScanning = false }

        // In P2: present RoomCaptureView, get USDZ/JSON, upload
        // Mock: simulate a successful scan
        do {
            try await client.uploadScan(projectId: projectId, scanData: [
                "rooms": [["width": 4.2, "length": 5.8, "height": 2.7]],
            ])
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            // TODO: error handling
        }
    }

    private func skipScan() async {
        guard let projectId = projectState.projectId else { return }
        do {
            try await client.skipScan(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            // TODO: error handling
        }
    }
}
