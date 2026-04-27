import Foundation
import SwiftUI
@_exported import RemoLiDAR

#if canImport(AVFoundation)
import AVFoundation
#endif

#if canImport(RoomPlan)
import RoomPlan
#endif

/// Scan screen — uses real LiDAR via RoomCaptureView when the device supports
/// it, and falls back to a sample-bathroom fixture otherwise (simulator,
/// older iPhones). Exports the scan via RoomPlanExporter and uploads via
/// TileWorkflowClient.
/// Aggregate payload passed from scan → review so the review screen has
/// everything it needs (real wall positions for the floor plan + any
/// detected objects the user may want to tile).
struct TileScanOutcome {
    let walls: [ScannedWallSegment]
    let tubs: [TileableObject]
}

struct TileScanScreen: View {
    let projectId: String
    let client: any TileWorkflowClient
    let onAdvance: (TileScanOutcome) -> Void

    enum ScanPhase: Equatable {
        case ready
        case scanning
        case processing
        case uploading
        case failed(String)
    }

    @State private var phase: ScanPhase = .ready
    #if canImport(RoomPlan)
    @State private var sessionRef = CaptureSessionRef()
    #endif

    private var hasLiDAR: Bool { LiDARCapability.isSupported }

    var body: some View {
        ZStack {
            mainContent

            if case .processing = phase {
                loadingOverlay("Processing scan…")
            } else if case .uploading = phase {
                loadingOverlay("Uploading…")
            }
        }
        #if canImport(RoomPlan)
        .fullScreenCover(isPresented: Binding(
            get: { phase == .scanning },
            set: { if !$0 && phase == .scanning { phase = .ready } }
        )) {
            roomCaptureScreen
        }
        #endif
        .alert("Scan Error", isPresented: Binding(
            get: { if case .failed = phase { return true } else { return false } },
            set: { if !$0 { phase = .ready } }
        )) {
            Button("Retry") { phase = .ready }
            Button("Use Sample", role: .destructive) {
                Task { await uploadSampleFixture() }
            }
        } message: {
            if case .failed(let msg) = phase { Text(msg) }
        }
    }

    @ViewBuilder
    private var mainContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                TileStepperBar(current: .scan)

                VStack(alignment: .leading, spacing: 8) {
                    Text("Scan Your Room")
                        .font(.title2.weight(.bold))
                    Text("Capture your bathroom with LiDAR. Every wall, door, and window becomes a surface in the tile-count math.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 20)

                // Viewfinder placeholder
                RoundedRectangle(cornerRadius: 16)
                    .fill(Color(.tertiarySystemBackground))
                    .aspectRatio(1, contentMode: .fit)
                    .overlay {
                        VStack(spacing: 12) {
                            Image(systemName: "cube.transparent")
                                .font(.system(size: 64))
                                .foregroundStyle(hasLiDAR ? Color.accentColor : .secondary)
                            Text(hasLiDAR ? "Tap Start Scan" : "LiDAR not available")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.horizontal, 20)

                VStack(spacing: 12) {
                    if hasLiDAR {
                        Button {
                            Task { await startLiDARScan() }
                        } label: {
                            primaryLabel(icon: "viewfinder", text: "Start LiDAR Scan")
                        }
                        .disabled(!(phase == .ready))
                        .accessibilityIdentifier("tile_scan_start")

                        Button {
                            Task { await uploadSampleFixture() }
                        } label: {
                            Text("Skip – use sample bathroom")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                        .accessibilityIdentifier("tile_scan_use_sample")
                    } else {
                        Button {
                            Task { await uploadSampleFixture() }
                        } label: {
                            primaryLabel(icon: "square.grid.2x2.fill", text: "Use Sample Bathroom")
                        }
                        .disabled(!(phase == .ready))
                        .accessibilityIdentifier("tile_scan_use_sample")

                        Text("LiDAR requires an iPhone Pro / iPad Pro. Falling back to a 2.4m × 1.8m sample bathroom for testdrive.")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                            .multilineTextAlignment(.center)
                    }
                }
                .padding(.horizontal, 20)

                Spacer(minLength: 40)
            }
            .padding(.top, 16)
        }
    }

    private func primaryLabel(icon: String, text: String) -> some View {
        HStack {
            Image(systemName: icon)
            Text(text).font(.headline)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 14)
        .background(Color.accentColor)
        .foregroundStyle(.white)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func loadingOverlay(_ msg: String) -> some View {
        ZStack {
            Color.black.opacity(0.3).ignoresSafeArea()
            ProgressView(msg)
                .padding(24)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
        }
    }

    #if canImport(RoomPlan)
    @ViewBuilder
    private var roomCaptureScreen: some View {
        ZStack {
            RoomCaptureViewWrapper(sessionRef: sessionRef) { result in
                onScanComplete(result)
            }
            .ignoresSafeArea()

            VStack {
                HStack {
                    Spacer()
                    Button {
                        sessionRef.stop()
                        phase = .ready
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title)
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, .white.opacity(0.3))
                    }
                    .padding(.trailing, 20)
                    .padding(.top, 60)
                    .accessibilityIdentifier("tile_scan_cancel")
                }
                Text("Walk slowly around the room")
                    .font(.subheadline)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))

                Spacer()

                VStack(spacing: 0) {
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
                    .padding(.top, 16)
                    .padding(.bottom, 8)
                }
                .frame(maxWidth: .infinity)
                .background(.ultraThinMaterial, ignoresSafeAreaEdges: .bottom)
            }
        }
    }
    #endif

    // MARK: - Scan flow

    private func startLiDARScan() async {
        #if canImport(AVFoundation)
        let granted = await requestCameraPermission()
        guard granted else {
            phase = .failed("Camera access required for room scanning. Enable in Settings > Privacy > Camera.")
            return
        }
        #endif
        phase = .scanning
    }

    #if canImport(RoomPlan)
    private func onScanComplete(_ result: Result<CapturedRoom, Error>) {
        switch result {
        case .success(let room):
            guard phase == .scanning else { return }
            phase = .processing
            Task {
                let scanDict = RoomPlanExporter.export(room)
                let wallSegments = WallSegmentsExtractor.extract(from: room)
                do {
                    let json = try JSONSerialization.data(withJSONObject: scanDict)
                    phase = .uploading
                    _ = try await client.uploadScan(projectId: projectId, scanJSON: json)
                    let tubs = Self.extractTileableObjects(from: scanDict)
                    onAdvance(TileScanOutcome(walls: wallSegments, tubs: tubs))
                    phase = .ready
                } catch {
                    phase = .failed("Upload failed: \(error.localizedDescription)")
                }
            }
        case .failure(let error):
            if error is CancellationError {
                phase = .ready
            } else {
                phase = .failed("Scan failed: \(error.localizedDescription)")
            }
        }
    }
    #endif

    private func uploadSampleFixture() async {
        phase = .uploading
        do {
            let data = try JSONSerialization.data(withJSONObject: Self.sampleBathroomJSON)
            _ = try await client.uploadScan(projectId: projectId, scanJSON: data)
            let tubs = Self.extractTileableObjects(from: Self.sampleBathroomJSON)
            // Synthetic wall segments matching the fixture's 2.4 × 1.8m room —
            // arranged around the perimeter so the floor plan renders.
            let walls = [
                ScannedWallSegment(patchId: "wall_0", startX: 0, startZ: 0, endX: 2.4, endZ: 0),
                ScannedWallSegment(patchId: "wall_1", startX: 2.4, startZ: 0, endX: 2.4, endZ: 1.8),
                ScannedWallSegment(patchId: "wall_2", startX: 2.4, startZ: 1.8, endX: 0, endZ: 1.8),
                ScannedWallSegment(patchId: "wall_3", startX: 0, startZ: 1.8, endX: 0, endZ: 0),
            ]
            onAdvance(TileScanOutcome(walls: walls, tubs: tubs))
            phase = .ready
        } catch {
            phase = .failed("Upload failed: \(error.localizedDescription)")
        }
    }

    /// Extract bathtub (and similar) objects from a scan JSON so the review
    /// screen can offer their exposed sides as tileable surfaces.
    static func extractTileableObjects(from scanDict: [String: Any]) -> [TileableObject] {
        guard let furniture = scanDict["furniture"] as? [[String: Any]] else { return [] }
        return furniture.enumerated().compactMap { (idx, item) -> TileableObject? in
            let type = (item["type"] as? String) ?? ""
            // Bathtubs are the obvious case; extend when we add showers,
            // vanity blocks, etc. as tileable carcasses.
            guard type == "bathtub" else { return nil }
            guard
                let w = item["width"] as? Double,
                let d = item["depth"] as? Double,
                let h = item["height"] as? Double
            else { return nil }
            return TileableObject(
                id: "tub_\(idx)",
                label: "Bathtub",
                widthM: w, depthM: d, heightM: h
            )
        }
    }

    #if canImport(AVFoundation)
    private func requestCameraPermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video)
        default: return false
        }
    }
    #endif

    private static let sampleBathroomJSON: [String: Any] = [
        "room": ["width": 2.4, "length": 1.8, "height": 2.4, "unit": "meters"],
        "walls": [
            ["id": "wall_0", "width": 2.4, "height": 2.4],
            ["id": "wall_1", "width": 1.8, "height": 2.4],
            ["id": "wall_2", "width": 2.4, "height": 2.4],
            ["id": "wall_3", "width": 1.8, "height": 2.4],
        ],
        "openings": [
            [
                "type": "door", "wall_id": "wall_2",
                "width": 0.9, "height": 2.1,
                "position": ["x": 0.3],
            ],
            [
                "type": "window", "wall_id": "wall_1",
                "width": 1.2, "height": 0.9,
                "position": ["x": 0.3, "y": 0.9],
            ],
        ],
        "furniture": [
            ["type": "bathtub", "width": 1.7, "depth": 0.8, "height": 0.6],
        ],
        "surfaces": [["type": "floor"]],
        "floor_area_sqm": 4.32,
    ]
}
