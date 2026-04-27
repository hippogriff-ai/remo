import Foundation
import SwiftUI

/// Post-scan surface review: shows dimensions captured from LiDAR for every
/// wall + floor, opening counts, and lets the user add mask regions (niches,
/// tub surrounds, vanity, half-wall) that get excluded from the tile count.
struct TileSurfaceReviewScreen: View {
    let projectId: String
    let client: any TileWorkflowClient
    let state: TileWorkflowState
    let tileableObjects: [TileableObject]
    let scannedWalls: [ScannedWallSegment]
    let onAdvance: () -> Void
    let onStateChange: (TileWorkflowState) -> Void

    @State private var editingSurface: SurfacePatch?
    @State private var isSaving = false
    @State private var errorMessage: String?
    /// For each detected object, which sides the user wants to tile.
    @State private var selectedSides: [String: Set<TileableObject.Side>] = [:]

    private var walls: [SurfacePatch] { state.surfaces.filter { $0.kind == "wall" } }
    private var floors: [SurfacePatch] { state.surfaces.filter { $0.kind == "floor" } }

    @State private var selectedSurfaceId: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                TileStepperBar(current: .scan)

                VStack(alignment: .leading, spacing: 8) {
                    Text("Review Surfaces")
                        .font(.title2.weight(.bold))
                    Text("Your LiDAR scan captured \(walls.count) walls and \(floors.count) floor\(floors.count == 1 ? "" : "s"). Tap a row to highlight it in the floor plan above, or tap **Mask** to exclude a region.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 20)

                // Top-down floor plan — uses real LiDAR wall positions.
                // Tap a surface row below to highlight it here.
                FloorPlanView(
                    segments: scannedWalls,
                    selectedPatchId: selectedSurfaceId
                )
                .frame(height: 240)
                .padding(16)
                .background(Color(.tertiarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .padding(.horizontal, 20)

                // Walls first, then floor
                VStack(spacing: 10) {
                    ForEach(walls, id: \.patchId) { surface in
                        SurfaceRow(
                            surface: surface,
                            tint: .blue,
                            isSelected: selectedSurfaceId == surface.patchId,
                            onSelect: { selectedSurfaceId = surface.patchId },
                            onMaskTap: { editingSurface = surface }
                        )
                    }
                    ForEach(floors, id: \.patchId) { surface in
                        SurfaceRow(
                            surface: surface,
                            tint: .orange,
                            isSelected: selectedSurfaceId == surface.patchId,
                            onSelect: { selectedSurfaceId = surface.patchId },
                            onMaskTap: { editingSurface = surface }
                        )
                    }
                }
                .padding(.horizontal, 20)

                // Tileable objects (bathtub sides, etc.)
                if !tileableObjects.isEmpty {
                    tileableObjectsSection
                }

                // Adjacency note
                if !floors.isEmpty {
                    HStack(spacing: 8) {
                        Image(systemName: "link").foregroundStyle(.secondary)
                        Text("Walls meet the floor at a grout line. Tile counts are computed per surface and meet cleanly at the conjunction.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 20)
                }

                // Info banner
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "info.circle.fill")
                        .foregroundStyle(.orange)
                    Text("Not tiling the whole wall? Tap Mask to exclude a niche, tub surround, vanity area, or half-wall wainscot — those regions won't be counted.")
                        .font(.caption)
                }
                .padding(12)
                .background(Color.orange.opacity(0.08))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal, 20)

                if let errorMessage {
                    Text(errorMessage).font(.footnote).foregroundStyle(.red).padding(.horizontal, 20)
                }

                Button {
                    onAdvance()
                } label: {
                    HStack {
                        if isSaving { ProgressView().tint(.white) }
                        Text("Continue to tile specs")
                            .font(.headline)
                        Image(systemName: "arrow.right")
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(Color.accentColor)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .padding(.horizontal, 20)
                .accessibilityIdentifier("tile_review_continue")

                Spacer(minLength: 40)
            }
            .padding(.top, 16)
        }
        .sheet(item: $editingSurface) { surface in
            TileMaskEditorSheet(
                surface: surface,
                onSave: { updatedSurface in
                    Task { await saveSurfaces(replacing: updatedSurface) }
                    editingSurface = nil
                },
                onCancel: { editingSurface = nil }
            )
            .presentationDetents([.fraction(0.88), .large])
        }
    }

    private func saveSurfaces(replacing updated: SurfacePatch) async {
        isSaving = true
        defer { isSaving = false }
        let merged = mergeSurfaces(baseReplacement: updated)
        do {
            try await client.setSurfaces(projectId: projectId, surfaces: merged)
            let fresh = try await client.getState(projectId: projectId)
            onStateChange(fresh)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Build the full surfaces payload: scanned walls/floors (with the
    /// updated mask replacement for one, if given) + any tub-side patches
    /// the user has toggled on.
    private func mergeSurfaces(baseReplacement: SurfacePatch? = nil) -> [SurfacePatch] {
        let baseline: [SurfacePatch]
        if let rep = baseReplacement {
            baseline = state.surfaces.map { $0.patchId == rep.patchId ? rep : $0 }
        } else {
            // Preserve any mask edits already in state.surfaces.
            baseline = state.surfaces
        }
        let tubPatches = tileableObjects.flatMap { object in
            (selectedSides[object.id] ?? []).map { side in
                object.patch(for: side)
            }
        }
        // Filter out any stale tub patches from a previous selection.
        let scannedOnly = baseline.filter { patch in
            !tileableObjects.contains { patch.patchId.hasPrefix("\($0.id)_") }
        }
        return scannedOnly + tubPatches
    }

    private func saveTubSelection() async {
        isSaving = true
        defer { isSaving = false }
        do {
            try await client.setSurfaces(projectId: projectId, surfaces: mergeSurfaces())
            let fresh = try await client.getState(projectId: projectId)
            onStateChange(fresh)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    @ViewBuilder
    private var tileableObjectsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tileable objects")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 20)
            ForEach(tileableObjects) { object in
                TubSidesCard(
                    object: object,
                    selected: Binding(
                        get: { selectedSides[object.id] ?? [] },
                        set: { newValue in
                            selectedSides[object.id] = newValue
                            Task { await saveTubSelection() }
                        }
                    )
                )
                .padding(.horizontal, 20)
            }
        }
    }
}

// MARK: - Tub sides card

private struct TubSidesCard: View {
    let object: TileableObject
    @Binding var selected: Set<TileableObject.Side>

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "bathtub.fill")
                    .foregroundStyle(.cyan)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 2) {
                    Text(object.label).font(.headline)
                    Text(String(format: "%.2fm × %.2fm × %.2fm", object.widthM, object.depthM, object.heightM))
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !selected.isEmpty {
                    Text("\(selected.count) side\(selected.count == 1 ? "" : "s") tiled")
                        .font(.caption2)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.accentColor.opacity(0.15))
                        .foregroundStyle(Color.accentColor)
                        .clipShape(Capsule())
                }
            }
            Text("Select the exposed vertical faces to tile. Alcove tubs typically expose only the front; freestanding tubs expose all four.")
                .font(.caption)
                .foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                ForEach(TileableObject.Side.allCases) { side in
                    let isOn = selected.contains(side)
                    Button {
                        if isOn { selected.remove(side) } else { selected.insert(side) }
                    } label: {
                        HStack {
                            Image(systemName: isOn ? "checkmark.circle.fill" : "circle")
                            Text(side.displayName).font(.footnote.weight(.medium))
                            Spacer()
                            let sideWidth = (side == .front || side == .back) ? object.widthM : object.depthM
                            Text(String(format: "%.2f×%.2fm", sideWidth, object.heightM))
                                .font(.caption2.monospaced())
                                .foregroundStyle(.secondary)
                        }
                        .padding(10)
                        .background(isOn ? Color.accentColor.opacity(0.12) : Color(.secondarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .foregroundStyle(isOn ? Color.accentColor : Color.primary)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("tub_side_\(object.id)_\(side.rawValue)")
                }
            }
        }
        .padding(14)
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

private struct SurfaceRow: View {
    let surface: SurfacePatch
    let tint: Color
    let isSelected: Bool
    let onSelect: () -> Void
    let onMaskTap: () -> Void

    private var dims: (width: Double, height: Double) {
        let xs = surface.polygon.map(\.x)
        let ys = surface.polygon.map(\.y)
        return ((xs.max() ?? 0) - (xs.min() ?? 0), (ys.max() ?? 0) - (ys.min() ?? 0))
    }

    private var openingCount: Int {
        surface.holes.filter { $0.kind == .opening }.count
    }
    private var maskCount: Int {
        surface.holes.filter { $0.kind == .mask }.count
    }

    var body: some View {
        Button(action: onSelect) {
            HStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 8)
                    .fill(tint.opacity(isSelected ? 0.35 : 0.18))
                    .frame(width: 44, height: 44)
                    .overlay {
                        Image(systemName: surface.kind == "floor" ? "square.fill" : "rectangle.portrait.fill")
                            .foregroundStyle(tint)
                    }

                VStack(alignment: .leading, spacing: 2) {
                    Text(surface.patchId.capitalized)
                        .font(.headline)
                        .foregroundStyle(Color.primary)
                    Text(String(format: "%.2fm × %.2fm", dims.width, dims.height))
                        .font(.footnote.monospaced())
                        .foregroundStyle(.secondary)
                    if openingCount + maskCount > 0 {
                        HStack(spacing: 6) {
                            if openingCount > 0 {
                                Label("\(openingCount)", systemImage: "rectangle.portrait.badge.minus")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            if maskCount > 0 {
                                Label("\(maskCount)", systemImage: "rectangle.on.rectangle")
                                    .font(.caption2)
                                    .foregroundStyle(.blue)
                            }
                        }
                    }
                }
                Spacer()

                Button(action: onMaskTap) {
                    Text("Mask")
                        .font(.footnote.weight(.semibold))
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(Color.accentColor.opacity(0.15))
                        .foregroundStyle(Color.accentColor)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("tile_surface_mask_\(surface.patchId)")
            }
            .padding(14)
            .background(Color(.systemBackground))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(isSelected ? tint : Color.clear, lineWidth: 2)
            )
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("tile_surface_row_\(surface.patchId)")
    }
}

// MARK: - Floor plan

/// Top-down 2D floor plan rendered from the actual LiDAR-captured wall
/// transforms (ScannedWallSegment with world-space start/end points).
/// Works for any room shape — rectangular, L-shape, alcoves, etc. — because
/// each wall is placed at its real position, not approximated.
private struct FloorPlanView: View {
    let segments: [ScannedWallSegment]
    let selectedPatchId: String?

    var body: some View {
        GeometryReader { geo in
            if segments.isEmpty {
                VStack {
                    Image(systemName: "cube.transparent")
                        .font(.largeTitle)
                        .foregroundStyle(.tertiary)
                    Text("Floor plan unavailable — scan with LiDAR to populate.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(width: geo.size.width, height: geo.size.height)
            } else {
                let (origin, scale) = fitTransform(for: segments, in: geo.size)
                ZStack {
                    // Filled floor polygon (connect segments start→start)
                    Path { path in
                        guard let first = segments.first else { return }
                        path.move(to: project(first.startX, first.startZ, origin: origin, scale: scale))
                        for seg in segments {
                            path.addLine(to: project(seg.endX, seg.endZ, origin: origin, scale: scale))
                        }
                        path.closeSubpath()
                    }
                    .fill(Color.orange.opacity(0.08))

                    // Each wall drawn at its true position
                    ForEach(segments, id: \.patchId) { seg in
                        let isSelected = seg.patchId == selectedPatchId
                        let start = project(seg.startX, seg.startZ, origin: origin, scale: scale)
                        let end = project(seg.endX, seg.endZ, origin: origin, scale: scale)
                        Path { path in
                            path.move(to: start)
                            path.addLine(to: end)
                        }
                        .stroke(
                            isSelected ? Color.accentColor : Color.primary,
                            style: StrokeStyle(lineWidth: isSelected ? 5 : 3, lineCap: .round)
                        )

                        let mid = CGPoint(x: (start.x + end.x) / 2, y: (start.y + end.y) / 2)
                        Text(seg.patchId.replacingOccurrences(of: "wall_", with: "W"))
                            .font(.caption2.weight(.bold).monospaced())
                            .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)
                            .padding(.horizontal, 4)
                            .padding(.vertical, 1)
                            .background(Color(.systemBackground).opacity(0.85))
                            .clipShape(Capsule())
                            .position(mid)
                    }
                }
            }
        }
    }

    private func project(_ x: Double, _ z: Double, origin: CGPoint, scale: CGFloat) -> CGPoint {
        // Floor plan convention: world X → screen X, world Z → screen Y.
        CGPoint(x: origin.x + CGFloat(x) * scale, y: origin.y + CGFloat(z) * scale)
    }

    private func fitTransform(for segments: [ScannedWallSegment], in size: CGSize) -> (CGPoint, CGFloat) {
        let xs = segments.flatMap { [$0.startX, $0.endX] }
        let zs = segments.flatMap { [$0.startZ, $0.endZ] }
        guard let minX = xs.min(), let maxX = xs.max(),
              let minZ = zs.min(), let maxZ = zs.max(),
              maxX > minX, maxZ > minZ else {
            return (CGPoint(x: size.width / 2, y: size.height / 2), 1)
        }
        let pad: CGFloat = 32
        let scaleX = (size.width - pad * 2) / CGFloat(maxX - minX)
        let scaleZ = (size.height - pad * 2) / CGFloat(maxZ - minZ)
        let scale = min(scaleX, scaleZ)
        let width = CGFloat(maxX - minX) * scale
        let height = CGFloat(maxZ - minZ) * scale
        let originX = (size.width - width) / 2 - CGFloat(minX) * scale
        let originY = (size.height - height) / 2 - CGFloat(minZ) * scale
        return (CGPoint(x: originX, y: originY), scale)
    }
}

// MARK: - Mask editor sheet

struct TileMaskEditorSheet: View {
    let surface: SurfacePatch
    let onSave: (SurfacePatch) -> Void
    let onCancel: () -> Void

    @State private var workingHoles: [TileHole]
    /// Active drag rectangle while the user is drawing a free-form mask, in
    /// surface-local meters. nil when not dragging.
    @State private var dragRect: CGRect?
    /// The rectangle the user just drew and is deciding to commit or redraw.
    @State private var pendingMask: CGRect?
    @State private var pendingLabel: String = "Custom mask"

    init(surface: SurfacePatch, onSave: @escaping (SurfacePatch) -> Void, onCancel: @escaping () -> Void) {
        self.surface = surface
        self.onSave = onSave
        self.onCancel = onCancel
        _workingHoles = State(initialValue: surface.holes)
    }

    private var dims: (width: Double, height: Double) {
        let xs = surface.polygon.map(\.x)
        let ys = surface.polygon.map(\.y)
        return ((xs.max() ?? 0) - (xs.min() ?? 0), (ys.max() ?? 0) - (ys.min() ?? 0))
    }

    private var masks: [TileHole] {
        workingHoles.filter { $0.kind == .mask }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Canvas sits OUTSIDE the ScrollView so its drag gesture
                // isn't swallowed by the scroll pan. This was the bug the
                // user hit — ScrollView consumed the drag as a scroll
                // attempt and the rectangle never drew.
                VStack(alignment: .leading, spacing: 6) {
                    Text("Drag on the surface to draw a mask")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    surfacePreview
                    if let pending = pendingMask {
                        pendingMaskControls(pending)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 16)

                Divider().padding(.top, 12)

                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {

                    // Preset chips
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Or use a preset").font(.caption).foregroundStyle(.secondary)
                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                            ForEach(Self.presets, id: \.name) { preset in
                                Button {
                                    addPreset(preset)
                                } label: {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(preset.name)
                                            .font(.footnote.weight(.semibold))
                                        Text(preset.summary)
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(10)
                                    .background(Color(.secondarySystemBackground))
                                    .clipShape(RoundedRectangle(cornerRadius: 10))
                                }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("tile_mask_preset_\(preset.name.lowercased().replacingOccurrences(of: " ", with: "_"))")
                            }
                        }
                    }
                    .padding(.horizontal, 20)

                    // Current masks list
                    if !masks.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Added masks").font(.caption).foregroundStyle(.secondary)
                            ForEach(Array(masks.enumerated()), id: \.offset) { _, mask in
                                HStack {
                                    Image(systemName: "rectangle.on.rectangle")
                                        .foregroundStyle(.blue)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(mask.label).font(.footnote.weight(.medium))
                                        Text(String(format: "%.2fm × %.2fm at (%.2f, %.2f)",
                                                   mask.widthM, mask.heightM, mask.xM, mask.yM))
                                            .font(.caption2.monospaced())
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Button {
                                        workingHoles.removeAll { other in
                                            other.kind == .mask
                                                && other.xM == mask.xM
                                                && other.yM == mask.yM
                                                && other.label == mask.label
                                        }
                                    } label: {
                                        Image(systemName: "trash")
                                            .foregroundStyle(.red)
                                    }
                                }
                                .padding(10)
                                .background(Color(.secondarySystemBackground))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                        }
                        .padding(.horizontal, 20)
                    }

                    Spacer(minLength: 20)
                    }
                    .padding(.top, 16)
                }
            }
            .navigationTitle("\(surface.patchId.capitalized) masks")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel", action: onCancel)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        var updated = surface
                        updated = SurfacePatch(
                            patchId: updated.patchId,
                            kind: updated.kind,
                            polygon: updated.polygon,
                            holes: workingHoles,
                            axis: updated.axis,
                            origin: updated.origin,
                            normalX: updated.normalX,
                            normalY: updated.normalY,
                            normalZ: updated.normalZ
                        )
                        onSave(updated)
                    }
                    .bold()
                    .accessibilityIdentifier("tile_mask_done")
                }
            }
        }
    }

    private var surfacePreview: some View {
        GeometryReader { geo in
            let w = dims.width, h = dims.height
            let scale = min(geo.size.width / w, 240 / h)
            let canvasWidth = w * scale
            let canvasHeight = h * scale

            HStack {
                Spacer(minLength: 0)
                canvasContent(canvasWidth: canvasWidth, canvasHeight: canvasHeight, scale: scale)
                Spacer(minLength: 0)
            }
        }
        .frame(height: 240)
    }

    /// The canvas as its own tight view with a named coordinate space — so
    /// gesture coordinates are always in canvas-local points, never offset
    /// by whatever parent frame is wrapping it.
    @ViewBuilder
    private func canvasContent(canvasWidth: Double, canvasHeight: Double, scale: Double) -> some View {
        ZStack(alignment: .topLeading) {
            Rectangle()
                .fill(Color(.tertiarySystemBackground))
                .overlay(
                    GridLines(spacing: 20)
                        .stroke(Color.secondary.opacity(0.2), lineWidth: 0.5)
                )
                .overlay(Rectangle().stroke(Color.secondary, lineWidth: 1))

            ForEach(Array(workingHoles.enumerated()), id: \.offset) { _, hole in
                let isMask = hole.kind == .mask
                Rectangle()
                    .fill(isMask ? Color.blue.opacity(0.25) : Color.gray.opacity(0.2))
                    .overlay(
                        Rectangle().stroke(
                            isMask ? Color.blue : Color.gray,
                            style: StrokeStyle(lineWidth: 1, dash: [3, 3])
                        )
                    )
                    .frame(width: hole.widthM * scale, height: hole.heightM * scale)
                    .position(
                        x: (hole.xM + hole.widthM / 2) * scale,
                        y: (hole.yM + hole.heightM / 2) * scale
                    )
            }

            // Pending mask (solid fill + corner handles for resize)
            if let rect = pendingMask {
                pendingMaskInteractive(rect: rect, scale: scale,
                                       canvasWidth: canvasWidth, canvasHeight: canvasHeight)
            } else if let rect = dragRect {
                // Only show drag preview when no pending mask exists yet
                Rectangle()
                    .fill(Color.accentColor.opacity(0.2))
                    .overlay(Rectangle().stroke(
                        Color.accentColor,
                        style: StrokeStyle(lineWidth: 2, dash: [4, 2])
                    ))
                    .frame(width: rect.width * scale, height: rect.height * scale)
                    .position(
                        x: (rect.minX + rect.width / 2) * scale,
                        y: (rect.minY + rect.height / 2) * scale
                    )
            }
        }
        .frame(width: canvasWidth, height: canvasHeight)
        .contentShape(Rectangle())
        .coordinateSpace(name: "maskCanvas")
        .highPriorityGesture(
            DragGesture(minimumDistance: 2, coordinateSpace: .named("maskCanvas"))
                .onChanged { value in
                    // Block new drags while a pending rect exists — user
                    // should resize that one via handles, not redraw.
                    guard pendingMask == nil else { return }
                    let start = CGPoint(
                        x: max(0, min(value.startLocation.x, canvasWidth)) / scale,
                        y: max(0, min(value.startLocation.y, canvasHeight)) / scale
                    )
                    let current = CGPoint(
                        x: max(0, min(value.location.x, canvasWidth)) / scale,
                        y: max(0, min(value.location.y, canvasHeight)) / scale
                    )
                    dragRect = CGRect(
                        x: min(start.x, current.x),
                        y: min(start.y, current.y),
                        width: abs(current.x - start.x),
                        height: abs(current.y - start.y)
                    )
                }
                .onEnded { _ in
                    if let r = dragRect, r.width > 0.05, r.height > 0.05 {
                        pendingMask = r
                    }
                    dragRect = nil
                }
        )
    }

    /// Pending mask with 4 corner handles (resize) + drag-interior (move).
    /// All handle drags operate in canvas-local coords via the "maskCanvas"
    /// coordinate space set on the ZStack above.
    @ViewBuilder
    private func pendingMaskInteractive(
        rect: CGRect, scale: Double, canvasWidth: Double, canvasHeight: Double
    ) -> some View {
        let surfaceMaxX = dims.width
        let surfaceMaxY = dims.height

        Rectangle()
            .fill(Color.accentColor.opacity(0.3))
            .overlay(Rectangle().stroke(Color.accentColor, lineWidth: 2))
            .frame(width: rect.width * scale, height: rect.height * scale)
            .position(
                x: (rect.minX + rect.width / 2) * scale,
                y: (rect.minY + rect.height / 2) * scale
            )
            .gesture(
                DragGesture(minimumDistance: 0, coordinateSpace: .named("maskCanvas"))
                    .onChanged { value in
                        let dx = value.translation.width / scale
                        let dy = value.translation.height / scale
                        var newX = rect.minX + dx
                        var newY = rect.minY + dy
                        newX = min(max(0, newX), surfaceMaxX - rect.width)
                        newY = min(max(0, newY), surfaceMaxY - rect.height)
                        pendingMask = CGRect(
                            x: newX, y: newY, width: rect.width, height: rect.height
                        )
                    }
            )

        // 4 corner handles
        ForEach(Corner.allCases, id: \.self) { corner in
            handleView(rect: rect, corner: corner, scale: scale,
                       surfaceMaxX: surfaceMaxX, surfaceMaxY: surfaceMaxY)
        }
    }

    enum Corner: CaseIterable { case tl, tr, bl, br }

    @ViewBuilder
    private func handleView(
        rect: CGRect, corner: Corner, scale: Double,
        surfaceMaxX: Double, surfaceMaxY: Double
    ) -> some View {
        let hx: Double = {
            switch corner { case .tl, .bl: return rect.minX; case .tr, .br: return rect.maxX }
        }()
        let hy: Double = {
            switch corner { case .tl, .tr: return rect.minY; case .bl, .br: return rect.maxY }
        }()

        Circle()
            .fill(Color.white)
            .overlay(Circle().stroke(Color.accentColor, lineWidth: 2))
            .frame(width: 22, height: 22)
            .position(x: hx * scale, y: hy * scale)
            .highPriorityGesture(
                DragGesture(minimumDistance: 0, coordinateSpace: .named("maskCanvas"))
                    .onChanged { value in
                        let cx = min(max(0, value.location.x / scale), surfaceMaxX)
                        let cy = min(max(0, value.location.y / scale), surfaceMaxY)
                        var newRect = rect
                        switch corner {
                        case .tl:
                            let right = rect.maxX
                            let bottom = rect.maxY
                            let newMinX = min(cx, right - 0.05)
                            let newMinY = min(cy, bottom - 0.05)
                            newRect = CGRect(x: newMinX, y: newMinY,
                                             width: right - newMinX, height: bottom - newMinY)
                        case .tr:
                            let left = rect.minX
                            let bottom = rect.maxY
                            let newMaxX = max(cx, left + 0.05)
                            let newMinY = min(cy, bottom - 0.05)
                            newRect = CGRect(x: left, y: newMinY,
                                             width: newMaxX - left, height: bottom - newMinY)
                        case .bl:
                            let right = rect.maxX
                            let top = rect.minY
                            let newMinX = min(cx, right - 0.05)
                            let newMaxY = max(cy, top + 0.05)
                            newRect = CGRect(x: newMinX, y: top,
                                             width: right - newMinX, height: newMaxY - top)
                        case .br:
                            let left = rect.minX
                            let top = rect.minY
                            let newMaxX = max(cx, left + 0.05)
                            let newMaxY = max(cy, top + 0.05)
                            newRect = CGRect(x: left, y: top,
                                             width: newMaxX - left, height: newMaxY - top)
                        }
                        pendingMask = newRect
                    }
            )
    }

    @ViewBuilder
    private func pendingMaskControls(_ rect: CGRect) -> some View {
        VStack(spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("New mask").font(.footnote.weight(.semibold))
                    Text(String(format: "%.2fm × %.2fm at (%.2f, %.2f)",
                                rect.width, rect.height, rect.minX, rect.minY))
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            TextField("Label (e.g. Niche, Shower bench)", text: $pendingLabel)
                .textFieldStyle(.roundedBorder)

            HStack {
                Button("Discard") {
                    pendingMask = nil
                    pendingLabel = "Custom mask"
                }
                .buttonStyle(.bordered)
                Spacer()
                Button {
                    workingHoles.append(
                        TileHole(
                            xM: rect.minX, yM: rect.minY,
                            widthM: rect.width, heightM: rect.height,
                            label: pendingLabel.isEmpty ? "Custom mask" : pendingLabel,
                            kind: .mask
                        )
                    )
                    pendingMask = nil
                    pendingLabel = "Custom mask"
                } label: {
                    Label("Add Mask", systemImage: "checkmark.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("tile_mask_add_pending")
            }
        }
        .padding(10)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func addPreset(_ preset: Preset) {
        let (w, h) = dims
        let holeW = min(preset.width, w - 0.1)
        let holeH = min(preset.height, h - 0.1)
        let x: Double
        let y: Double
        switch preset.anchor {
        case .center:
            x = max(0.05, (w - holeW) / 2)
            y = max(0.05, (h - holeH) / 2)
        case .bottom:
            x = max(0.05, (w - holeW) / 2)
            y = 0.0
        case .fullWidthBottom:
            x = 0.0
            y = 0.0
        }
        workingHoles.append(
            TileHole(
                xM: x, yM: y,
                widthM: preset.anchor == .fullWidthBottom ? w : holeW,
                heightM: holeH,
                label: preset.name,
                kind: .mask
            )
        )
    }

    struct Preset {
        let name: String
        let width: Double
        let height: Double
        let anchor: Anchor
        enum Anchor { case center, bottom, fullWidthBottom }
        var summary: String {
            switch anchor {
            case .center: return String(format: "%.0f × %.0f cm", width * 100, height * 100)
            case .bottom: return String(format: "%.0f × %.0f cm · bottom", width * 100, height * 100)
            case .fullWidthBottom: return String(format: "full × %.0f cm · bottom", height * 100)
            }
        }
    }

    static let presets: [Preset] = [
        Preset(name: "Niche", width: 0.4, height: 0.6, anchor: .center),
        Preset(name: "Tub surround", width: 1.8, height: 0.55, anchor: .bottom),
        Preset(name: "Vanity area", width: 1.2, height: 0.9, anchor: .bottom),
        Preset(name: "Half-wall", width: 0, height: 1.0, anchor: .fullWidthBottom),
    ]
}

// Need SurfacePatch to be Identifiable for .sheet(item:). Already Hashable via Codable conformance.
extension SurfacePatch: Identifiable {
    public var id: String { patchId }
}

/// Fine blueprint grid drawn across the mask editor preview.
private struct GridLines: Shape {
    let spacing: CGFloat
    func path(in rect: CGRect) -> Path {
        var p = Path()
        var x: CGFloat = spacing
        while x < rect.width {
            p.move(to: CGPoint(x: x, y: 0))
            p.addLine(to: CGPoint(x: x, y: rect.height))
            x += spacing
        }
        var y: CGFloat = spacing
        while y < rect.height {
            p.move(to: CGPoint(x: 0, y: y))
            p.addLine(to: CGPoint(x: rect.width, y: y))
            y += spacing
        }
        return p
    }
}
