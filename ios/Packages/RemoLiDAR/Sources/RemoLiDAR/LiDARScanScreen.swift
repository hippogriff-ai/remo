import Foundation
import SwiftUI
import RemoModels
import RemoNetworking

#if canImport(ARKit)
import ARKit
#endif

/// LiDAR scan screen: device capability check, scan flow, skip option.
public struct LiDARScanScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var isScanning = false
    @State private var showSkipConfirmation = false
    @State private var errorMessage: String?

    public init(projectState: ProjectState, client: any WorkflowClientProtocol) {
        self.projectState = projectState
        self.client = client
    }

    private var hasLiDAR: Bool {
        #if DEBUG
        // Fixture mode: always show scan button so Maestro can trigger fixture path
        if UserDefaults.standard.string(forKey: "lidar-fixture") != nil {
            return true
        }
        #endif
        #if canImport(ARKit)
        return ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
        #else
        return false
        #endif
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
                .accessibilityIdentifier("scan_start")
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
            .accessibilityHint("Skip room scanning. Furniture fit information won't be available.")
            .accessibilityIdentifier("scan_skip")
        }
        .padding()
        .navigationTitle("Room Scan")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
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
        guard let projectId = projectState.projectId else {
            assertionFailure("startScan() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        isScanning = true
        defer { isScanning = false }

        do {
            let scanData: [String: Any]

            #if DEBUG
            if let fixtureName = UserDefaults.standard.string(forKey: "lidar-fixture") {
                // TEST-ONLY: Load saved fixture JSON for Maestro/CI automated testing.
                // Bypasses RoomCaptureView — fixture is a snapshot of real RoomPlan output
                // captured once from a physical device (B1) or hand-written reference (B3).
                scanData = try Self.loadFixture(named: fixtureName)
            } else {
                // Mock: simulate a successful scan (replaced by real RoomCaptureView in Phase A)
                scanData = [
                    "room": ["width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters"] as [String: Any],
                    "walls": [] as [[String: Any]],
                    "openings": [] as [[String: Any]],
                    "floor_area_sqm": 24.36,
                ]
            }
            #else
            #warning("Release build uses mock scan data — replace with real RoomCaptureView in Phase A")
            scanData = [
                "room": ["width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters"] as [String: Any],
                "walls": [] as [[String: Any]],
                "openings": [] as [[String: Any]],
                "floor_area_sqm": 24.36,
            ]
            #endif

            try await client.uploadScan(projectId: projectId, scanData: scanData)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    #if DEBUG
    /// Load a fixture JSON file from the app bundle (test-only).
    private static func loadFixture(named name: String) throws -> [String: Any] {
        guard let url = Bundle.main.url(forResource: name, withExtension: "json") else {
            throw NSError(
                domain: "LiDAR", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Fixture '\(name)' not found in bundle"]
            )
        }
        let data = try Data(contentsOf: url)
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NSError(
                domain: "LiDAR", code: 2,
                userInfo: [NSLocalizedDescriptionKey: "Fixture '\(name)' is not a valid JSON object"]
            )
        }
        return json
    }
    #endif

    private func skipScan() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("skipScan() called without projectId")
            errorMessage = "Project not initialized"
            return
        }
        do {
            try await client.skipScan(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

#Preview {
    NavigationStack {
        LiDARScanScreen(projectState: .preview(step: .scan), client: MockWorkflowClient(delay: .zero))
    }
}
