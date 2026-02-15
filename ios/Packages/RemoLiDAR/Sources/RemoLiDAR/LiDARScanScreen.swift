import Foundation
import os
import SwiftUI
import RemoModels
import RemoNetworking

#if canImport(ARKit)
import ARKit
#endif

#if canImport(RoomPlan)
import RoomPlan
#endif

#if os(iOS)
import AVFoundation
#endif

private let scanLogger = Logger(subsystem: "com.remo.lidar", category: "scan")

/// Scan state machine for the LiDAR flow.
enum ScanState: Equatable {
    case ready
    case scanning
    case processing
    case uploading
    case failed(String)
}

/// LiDAR scan screen: device capability check, scan flow, skip option.
public struct LiDARScanScreen: View {
    @Bindable var projectState: ProjectState
    let client: any WorkflowClientProtocol

    @State private var scanState: ScanState = .ready
    @State private var showSkipConfirmation = false
    @State private var scanTimeoutTask: Task<Void, Never>?
    #if canImport(RoomPlan)
    @State private var sessionRef = CaptureSessionRef()
    #endif
    @Environment(\.scenePhase) private var scenePhase

    /// Scan timeout in seconds. If scanning state persists this long without a
    /// delegate callback, auto-fail to prevent the user getting stuck (e.g. when
    /// RoomPlan crashes internally without calling didEndWith).
    private static let scanTimeoutSeconds: UInt64 = 120

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
        ZStack {
            mainContent

            if scanState == .processing || scanState == .uploading {
                Color.black.opacity(0.3).ignoresSafeArea()
                ProgressView(scanState == .processing ? "Processing scan..." : "Uploading...")
                    .padding(24)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }
        #if canImport(RoomPlan)
        .fullScreenCover(isPresented: Binding(
            get: { scanState == .scanning },
            set: { if !$0 && scanState == .scanning { scanState = .ready } }
        )) {
            ZStack {
                RoomCaptureViewWrapper(sessionRef: sessionRef) { result in
                    onScanComplete(result)
                }
                .ignoresSafeArea()

                VStack {
                    HStack {
                        Spacer()
                        Button {
                            scanLogger.info("user cancelled scan manually")
                            sessionRef.stop()
                            scanTimeoutTask?.cancel()
                            scanTimeoutTask = nil
                            scanState = .ready
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.title)
                                .symbolRenderingMode(.palette)
                                .foregroundStyle(.white, .white.opacity(0.3))
                        }
                        .padding(.trailing, 20)
                        .padding(.top, 60)
                        .accessibilityLabel("Cancel scan")
                        .accessibilityIdentifier("scan_cancel")
                    }

                    Text("Walk slowly around the room")
                        .font(.subheadline)
                        .padding(.horizontal, 20)
                        .padding(.vertical, 10)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))

                    Spacer()

                    Button {
                        sessionRef.stop()
                    } label: {
                        Label("Done Scanning", systemImage: "checkmark.circle.fill")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .tint(.green)
                    .padding(.horizontal, 40)
                    .padding(.vertical, 16)
                    .frame(maxWidth: .infinity)
                    .background(.ultraThinMaterial)
                }
            }
        }
        #endif
        .alert("Scan Error", isPresented: Binding(
            get: { if case .failed = scanState { return true } else { return false } },
            set: { if !$0 { scanState = .ready } }
        )) {
            Button("Retry") { scanState = .ready }
            Button("Skip Scan", role: .destructive) {
                Task { await skipScan() }
            }
        } message: {
            if case .failed(let msg) = scanState {
                Text(msg)
            }
        }
        .alert("Skip Room Scan?", isPresented: $showSkipConfirmation) {
            Button("Skip", role: .destructive) {
                Task { await skipScan() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Without a scan, furniture sizing won't be available in your shopping list.")
        }
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase != .active && scanState == .scanning {
                scanTimeoutTask?.cancel()
                scanTimeoutTask = nil
                scanState = .failed("Scan interrupted. Please try again.")
            }
        }
    }

    private var mainContent: some View {
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
                .disabled(scanState != .ready)
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
            .disabled(scanState != .ready)
            .accessibilityHint("Skip room scanning. Furniture fit information won't be available.")
            .accessibilityIdentifier("scan_skip")
        }
        .padding()
        .navigationTitle("Room Scan")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }

    // MARK: - Scan flow

    private func startScan() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("startScan() called without projectId")
            scanState = .failed("Project not initialized")
            return
        }

        #if DEBUG
        if let fixtureName = UserDefaults.standard.string(forKey: "lidar-fixture") {
            // TEST-ONLY: Load saved fixture JSON for Maestro/CI automated testing.
            // Bypasses RoomCaptureView — fixture is a snapshot of real RoomPlan output
            // captured once from a physical device (B1) or hand-written reference (B3).
            scanLogger.info("startScan: using fixture '\(fixtureName, privacy: .public)'")
            scanState = .uploading
            do {
                let scanData = try Self.loadFixture(named: fixtureName)
                try await client.uploadScan(projectId: projectId, scanData: scanData)
                let newState = try await client.getState(projectId: projectId)
                projectState.apply(newState)
                scanState = .ready
            } catch {
                scanState = .failed(error.localizedDescription)
            }
            return
        }
        #endif

        #if os(iOS)
        guard await checkCameraPermission() else { return }
        #endif

        // Present RoomCaptureView via fullScreenCover
        scanLogger.info("startScan: presenting RoomCaptureView")
        scanState = .scanning
        startScanTimeout()
    }

    private func startScanTimeout() {
        scanTimeoutTask?.cancel()
        scanTimeoutTask = Task {
            do {
                try await Task.sleep(nanoseconds: Self.scanTimeoutSeconds * 1_000_000_000)
                if scanState == .scanning {
                    scanLogger.error("scan timeout after \(Self.scanTimeoutSeconds)s — auto-failing")
                    #if canImport(RoomPlan)
                    sessionRef.stop()
                    #endif
                    scanState = .failed("Scan timed out. Please try again or skip this step.")
                }
            } catch {
                // Task cancelled — scan completed or user dismissed before timeout
            }
        }
    }

    #if canImport(RoomPlan)
    private func onScanComplete(_ result: Result<CapturedRoom, Error>) {
        scanTimeoutTask?.cancel()
        scanTimeoutTask = nil
        switch result {
        case .success(let capturedRoom):
            // Guard: ignore late success callbacks after backgrounding interrupted the scan
            guard scanState == .scanning else {
                scanLogger.warning("ignoring success callback — scan already in state \(String(describing: scanState), privacy: .public)")
                return
            }
            scanState = .processing
            scanLogger.info("scan captured: \(capturedRoom.walls.count) walls, \(capturedRoom.doors.count + capturedRoom.windows.count + capturedRoom.openings.count) openings, \(capturedRoom.objects.count) objects, \(capturedRoom.floors.count) floors")
            Task {
                guard let projectId = projectState.projectId else {
                    assertionFailure("onScanComplete() called without projectId")
                    scanState = .failed("Project not initialized")
                    return
                }
                let scanData = RoomPlanExporter.export(capturedRoom)
                if let floorArea = scanData["floor_area_sqm"] as? Double, floorArea > 0 {
                    scanLogger.info("exported scan: floor_area=\(floorArea)m²")
                } else {
                    scanLogger.warning("exported scan: floor_area missing or zero — scan may be incomplete")
                }
                #if DEBUG
                // T7: Capture exported scan as fixture for later use.
                // Build to device with -capture-lidar-fixture launch arg, scan a room,
                // then pull captured_room.json from the app's Documents directory.
                if ProcessInfo.processInfo.arguments.contains("-capture-lidar-fixture") {
                    do {
                        let jsonData = try JSONSerialization.data(withJSONObject: scanData, options: [.prettyPrinted, .sortedKeys])
                        if let docsDir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first {
                            let url = docsDir.appendingPathComponent("captured_room.json")
                            try jsonData.write(to: url)
                            scanLogger.info("[FIXTURE] Saved to: \(url.path, privacy: .public)")
                        } else {
                            scanLogger.error("[FIXTURE] Documents directory not found — cannot save fixture")
                        }
                    } catch {
                        scanLogger.error("[FIXTURE] Save failed: \(error.localizedDescription, privacy: .public)")
                    }
                }
                #endif
                scanState = .uploading
                do {
                    try await client.uploadScan(projectId: projectId, scanData: scanData)
                    scanLogger.info("scan uploaded successfully")
                } catch {
                    scanLogger.error("scan upload failed: \(error.localizedDescription, privacy: .public)")
                    scanState = .failed("Scan upload failed. Please try again.")
                    return
                }
                do {
                    let newState = try await client.getState(projectId: projectId)
                    projectState.apply(newState)
                    scanState = .ready
                } catch {
                    // Upload succeeded but state refresh failed — recoverable
                    scanLogger.warning("state refresh failed after successful upload: \(error.localizedDescription, privacy: .public)")
                    scanState = .failed("Scan saved, but could not refresh. Please go back and return.")
                }
            }
        case .failure(let error):
            if error is CancellationError {
                scanLogger.info("scan processing was cancelled")
                // Only reset to .ready if not already .failed (backgrounding guard may
                // have set .failed("Scan interrupted...") before cancellation arrives)
                if case .failed = scanState { break } else { scanState = .ready }
            } else {
                scanLogger.error("scan failed: \(error.localizedDescription, privacy: .public)")
                scanState = .failed("Room scan failed. Please try again or skip this step.")
            }
        }
    }
    #endif

    // MARK: - Camera permission (T4)

    #if os(iOS)
    private func checkCameraPermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            return true
        case .notDetermined:
            let granted = await AVCaptureDevice.requestAccess(for: .video)
            if !granted {
                scanLogger.warning("camera permission denied by user on first prompt")
                scanState = .failed("Camera access required for room scanning. Enable in Settings > Privacy > Camera.")
            }
            return granted
        case .denied, .restricted:
            scanLogger.warning("camera permission denied or restricted")
            scanState = .failed("Camera access required for room scanning. Enable in Settings > Privacy > Camera.")
            return false
        @unknown default:
            scanLogger.warning("camera permission: unknown authorization status")
            scanState = .failed("Camera access required for room scanning. Enable in Settings > Privacy > Camera.")
            return false
        }
    }
    #endif

    // MARK: - Fixture loading

    #if DEBUG
    /// Load a fixture JSON file from the app bundle (test-only).
    static func loadFixture(named name: String) throws -> [String: Any] {
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

    // MARK: - Skip flow

    private func skipScan() async {
        guard let projectId = projectState.projectId else {
            assertionFailure("skipScan() called without projectId")
            scanState = .failed("Project not initialized")
            return
        }
        scanLogger.info("skipScan: skipping room scan")
        scanState = .uploading
        do {
            try await client.skipScan(projectId: projectId)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
            scanState = .ready
        } catch {
            scanLogger.error("skip scan failed: \(error.localizedDescription, privacy: .public)")
            scanState = .failed("Could not skip scan. Check your connection and try again.")
        }
    }
}

#Preview {
    NavigationStack {
        LiDARScanScreen(projectState: .preview(step: .scan), client: MockWorkflowClient(delay: .zero))
    }
}
