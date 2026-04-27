import Foundation
import SwiftUI

/// Router that listens to backend state and renders the correct phase screen.
public struct TileProjectFlowScreen: View {
    let projectId: String
    let client: any TileWorkflowClient

    @State private var state: TileWorkflowState?
    @State private var errorMessage: String?
    @State private var isLoading = true
    /// iOS-side flag — once the user taps "Continue to tile specs" on the
    /// surface-review screen, we advance the UX even though the backend
    /// already transitioned to specs phase on scan upload. Flipping back to
    /// false returns the user to the review screen.
    @State private var surfacesConfirmed = false
    /// Parallel flag for the specs → estimate transition. Backend advances
    /// on the /materials signal; iOS keeps the user on the specs screen
    /// until they confirm "See Estimate". Flipping back to false lets the
    /// user re-edit tile dimensions from the estimate screen.
    @State private var materialsConfirmed = false
    /// Bathtubs / other detected objects the user can choose to tile. Built
    /// from the scan JSON on iOS (backend doesn't expose furniture via query
    /// — it only persists surface patches).
    @State private var tileableObjects: [TileableObject] = []
    /// Real wall positions in world space, used to render a correct top-down
    /// floor plan. Preserved on iOS because the backend's SurfacePatch only
    /// stores surface-local polygon data (no world transforms).
    @State private var scannedWalls: [ScannedWallSegment] = []

    public init(projectId: String, client: any TileWorkflowClient) {
        self.projectId = projectId
        self.client = client
    }

    public var body: some View {
        Group {
            if let state {
                content(for: state)
                    .task(id: state.step) {
                        // Poll for state changes every 1s while on active steps.
                        await pollLoop()
                    }
            } else if isLoading {
                ProgressView("Loading project…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView(
                    "Could not load project",
                    systemImage: "exclamationmark.triangle",
                    description: Text(errorMessage ?? "")
                )
            }
        }
        .task { await refresh() }
        .navigationTitle("Replace Material")
        .navigationBarTitleDisplayMode(.inline)
        .alert("Error", isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    @ViewBuilder
    private func content(for state: TileWorkflowState) -> some View {
        let step = TileProjectStep(rawValue: state.step) ?? .scan
        // Scan phase OR specs phase but user hasn't confirmed surfaces yet.
        // Both map to the "scan/review" UX on iOS.
        if step == .scan {
            TileScanScreen(
                projectId: projectId, client: client,
                onAdvance: { outcome in
                    tileableObjects = outcome.tubs
                    scannedWalls = outcome.walls
                    Task { await refresh() }
                }
            )
        } else if step == .specs && !surfacesConfirmed {
            TileSurfaceReviewScreen(
                projectId: projectId, client: client, state: state,
                tileableObjects: tileableObjects,
                scannedWalls: scannedWalls,
                onAdvance: { surfacesConfirmed = true },
                onStateChange: { self.state = $0 }
            )
        } else if (step == .specs || step == .estimate) && !materialsConfirmed {
            TileSpecsScreen(
                projectId: projectId, client: client,
                onAdvance: { materialsConfirmed = true; Task { await refresh() } },
                onBack: { surfacesConfirmed = false }
            )
        } else if step == .estimate {
            TileEstimateScreen(
                projectId: projectId, client: client, state: state,
                onStateChange: { self.state = $0 },
                onBack: { materialsConfirmed = false }
            )
        } else if step == .render || step == .export || step == .completed {
            TilePlaceholderScreen(message: "Render + export coming in the next build.")
        } else {
            ContentUnavailableView(
                "Project Ended",
                systemImage: "clock.badge.xmark",
                description: Text("This tile project was cancelled or expired.")
            )
        }
    }

    private func refresh() async {
        do {
            state = try await client.getState(projectId: projectId)
            isLoading = false
        } catch {
            isLoading = false
            if state == nil { errorMessage = error.localizedDescription }
        }
    }

    private func pollLoop() async {
        // Only poll while on transient states; estimate is driven by explicit
        // user action and the estimate screen refreshes state on each toggle.
        guard let step = state.map({ TileProjectStep(rawValue: $0.step) }) ?? nil else { return }
        guard step == .scan || step == .specs else { return }
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            await refresh()
            if let s = state, TileProjectStep(rawValue: s.step) != step { return }
        }
    }
}

struct TilePlaceholderScreen: View {
    let message: String
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "hammer")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)
            Text(message)
                .font(.headline)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
