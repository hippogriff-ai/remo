import Foundation
import SwiftUI

/// The money screen — totals + policy toggles. Every toggle signals the
/// backend, which recomputes the estimate and returns the new state.
/// (Swift port of pack_surface for <16ms live re-render is PR 4.)
struct TileEstimateScreen: View {
    let projectId: String
    let client: any TileWorkflowClient
    let state: TileWorkflowState
    let onStateChange: (TileWorkflowState) -> Void
    let onBack: () -> Void

    @State private var isRefreshing = false
    @State private var errorMessage: String?
    @State private var confirmingRender = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                TileStepperBar(current: .estimate)

                HStack {
                    Button(action: onBack) {
                        Label("Edit tile specs", systemImage: "chevron.left")
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(Color.accentColor)
                    }
                    .accessibilityIdentifier("tile_estimate_back")
                    Spacer()
                }
                .padding(.horizontal, 20)

                headerBlock
                totalsCard
                if let estimate = state.estimate {
                    perSurfaceSummary(estimate)
                    mathBreakdown(estimate)
                }
                policiesCard

                if let errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal, 20)
                }

                primaryCTA

                Spacer(minLength: 40)
            }
            .padding(.top, 16)
        }
    }

    private var headerBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Estimate")
                .font(.title2.weight(.bold))
            Text("Totals for your scanned bathroom. Adjust the policies below — the math re-runs on every tap.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 20)
    }

    private var totalsCard: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                bigStat(
                    label: "Wall tile",
                    primary: "\(state.estimate?.wallBoxesTotal ?? 0)",
                    secondary: "boxes",
                    footnote: "\(state.estimate?.wallTilesTotal ?? 0) tiles",
                    id: "tile_wall_boxes_value"
                )
                Divider().frame(height: 72)
                bigStat(
                    label: "Floor tile",
                    primary: "\(state.estimate?.floorBoxesTotal ?? 0)",
                    secondary: "boxes",
                    footnote: "\(state.estimate?.floorTilesTotal ?? 0) tiles",
                    id: "tile_floor_boxes_value"
                )
            }
            .padding(.vertical, 16)

            Divider()

            HStack(spacing: 0) {
                miniStat(label: "Est. total", value: formattedTotal)
                Divider().frame(height: 40)
                miniStat(label: "Overage", value: "\(Int(overagePercent))%")
                Divider().frame(height: 40)
                miniStat(label: "Surfaces", value: "\(state.surfaces.count)")
            }
            .padding(.vertical, 10)
        }
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .padding(.horizontal, 20)
    }

    private func bigStat(label: String, primary: String, secondary: String, footnote: String, id: String) -> some View {
        VStack(spacing: 4) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(primary)
                    .font(.system(size: 42, weight: .bold, design: .rounded))
                    .monospacedDigit()
                Text(secondary).font(.footnote).foregroundStyle(.secondary)
            }
            Text(footnote).font(.caption2).foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity)
        .accessibilityIdentifier(id)
    }

    private func miniStat(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(label).font(.caption2).foregroundStyle(.tertiary)
            Text(value).font(.footnote.weight(.semibold)).monospacedDigit()
        }
        .frame(maxWidth: .infinity)
    }

    private func perSurfaceSummary(_ estimate: TileModeEstimate) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Per surface").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                .padding(.horizontal, 20)

            ForEach(estimate.wallPacks + estimate.floorPacks, id: \.surfaceId) { pack in
                HStack {
                    Image(systemName: pack.surfaceId.contains("floor") ? "square.fill" : "rectangle.portrait.fill")
                        .foregroundStyle(.secondary)
                    Text(pack.surfaceId)
                        .font(.subheadline.weight(.medium))
                    Spacer()
                    Text("\(pack.fullModules) full · \(pack.cutModules) cut · \(pack.tilesRequired) total")
                        .font(.footnote.monospaced())
                        .foregroundStyle(.secondary)
                }
                .padding(12)
                .background(Color(.systemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal, 20)
            }
        }
    }

    @State private var showBreakdown = false

    private func mathBreakdown(_ estimate: TileModeEstimate) -> some View {
        let wallPacked = estimate.wallPacks.reduce(0) { $0 + $1.tilesRequired }
        let floorPacked = estimate.floorPacks.reduce(0) { $0 + $1.tilesRequired }
        let wallArea = estimate.wallPacks.reduce(0.0) { sum, p in
            sum + p.rects.reduce(0.0) { $0 + $1.widthM * $1.heightM }
        }
        let floorArea = estimate.floorPacks.reduce(0.0) { sum, p in
            sum + p.rects.reduce(0.0) { $0 + $1.widthM * $1.heightM }
        }
        let wallTileArea = estimate.wallMaterial.moduleWidthM * estimate.wallMaterial.moduleHeightM
        let floorTileArea = estimate.floorMaterial.moduleWidthM * estimate.floorMaterial.moduleHeightM

        return DisclosureGroup(
            isExpanded: $showBreakdown,
            content: {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Wall math").font(.caption.weight(.semibold))
                    Text(String(format: "%.2f m² walls / %.3f m² per tile = %d packed tiles",
                                wallArea, wallTileArea, wallPacked))
                        .font(.caption.monospaced())
                    Text("× \(formatOverage()) overage = \(estimate.wallTilesTotal) tiles")
                        .font(.caption.monospaced())
                    Text("÷ \(estimate.wallMaterial.modulesPerUnit)/box = **\(estimate.wallBoxesTotal) boxes**")
                        .font(.caption.monospaced())

                    Divider().padding(.vertical, 4)

                    Text("Floor math").font(.caption.weight(.semibold))
                    Text(String(format: "%.2f m² floor / %.3f m² per tile = %d packed tiles",
                                floorArea, floorTileArea, floorPacked))
                        .font(.caption.monospaced())
                    Text("× \(formatOverage()) overage = \(estimate.floorTilesTotal) tiles")
                        .font(.caption.monospaced())
                    Text("÷ \(estimate.floorMaterial.modulesPerUnit)/box = **\(estimate.floorBoxesTotal) boxes**")
                        .font(.caption.monospaced())

                    if estimate.wallPacks.contains(where: { $0.surfaceId.hasPrefix("tub_") }) {
                        Divider().padding(.vertical, 4)
                        Text("Note: tub sides are counted as wall surfaces. If a tub side sits against a real wall, it's being double-counted — untick that side in the review screen.")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                }
                .padding(.top, 8)
            },
            label: {
                Text("Show math").font(.footnote.weight(.medium)).foregroundStyle(Color.accentColor)
            }
        )
        .padding(16)
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .padding(.horizontal, 20)
    }

    private func formatOverage() -> String {
        switch state.overagePolicy {
        case .flat10: return "10%"
        case .riskAdjusted: return "8-20% (risk-adj)"
        case .contractorTier:
            switch state.contractorTier {
            case .apprentice: return "15%"
            case .pro: return "12%"
            case .perfectionist: return "18%"
            case .none: return "12%"
            }
        }
    }

    private var policiesCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                Circle().fill(Color.green).frame(width: 8, height: 8)
                Text("LIVE POLICIES")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.secondary)
                    .tracking(0.6)
                if isRefreshing {
                    ProgressView().scaleEffect(0.7)
                }
            }

            policyRow(
                label: "Overage",
                selected: state.overagePolicy,
                options: OveragePolicy.allCases,
                idPrefix: "tile_overage",
                onSelect: { await setPolicy(overage: $0, tier: state.contractorTier, start: state.startingPointRule) }
            )

            if state.overagePolicy == .contractorTier {
                policyRow(
                    label: "Tier",
                    selected: state.contractorTier ?? .pro,
                    options: ContractorTier.allCases,
                    idPrefix: "tile_tier",
                    onSelect: { await setPolicy(overage: state.overagePolicy, tier: $0, start: state.startingPointRule) }
                )
            }

            policyRow(
                label: "Start",
                selected: state.startingPointRule,
                options: StartingPointRule.allCases,
                idPrefix: "tile_starting_rule",
                onSelect: { await setPolicy(overage: state.overagePolicy, tier: state.contractorTier, start: $0) }
            )
        }
        .padding(16)
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .padding(.horizontal, 20)
    }

    private func policyRow<T: RawRepresentable & CaseIterable & Hashable>(
        label: String,
        selected: T,
        options: T.AllCases,
        idPrefix: String,
        onSelect: @escaping (T) async -> Void
    ) -> some View where T.RawValue == String {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Array(options), id: \.self) { option in
                        Button {
                            Task { await onSelect(option) }
                        } label: {
                            Text(displayName(for: option))
                                .font(.footnote.weight(.medium))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(
                                    selected == option ? Color.accentColor : Color(.secondarySystemBackground)
                                )
                                .foregroundStyle(selected == option ? .white : Color.primary)
                                .clipShape(Capsule())
                        }
                        .accessibilityIdentifier("\(idPrefix)_\(option.rawValue)")
                    }
                }
            }
        }
    }

    private func displayName<T: RawRepresentable>(for option: T) -> String where T.RawValue == String {
        if let o = option as? OveragePolicy { return o.displayName }
        if let o = option as? ContractorTier { return o.displayName }
        if let o = option as? StartingPointRule { return o.displayName }
        return option.rawValue
    }

    private var primaryCTA: some View {
        Button {
            Task { await requestRender() }
        } label: {
            HStack {
                if confirmingRender { ProgressView().tint(.white) }
                Image(systemName: "wand.and.stars")
                Text(confirmingRender ? "Kicking off render…" : "Generate Render")
                    .font(.headline)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Color.accentColor)
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .disabled(confirmingRender || state.estimate == nil)
        .padding(.horizontal, 20)
        .accessibilityIdentifier("tile_estimate_confirm")
    }

    private var overagePercent: Double {
        guard let est = state.estimate else { return 0 }
        let wallPacked = est.wallPacks.reduce(0) { $0 + $1.tilesRequired }
        guard wallPacked > 0 else { return 0 }
        return Double(est.wallTilesTotal - wallPacked) / Double(wallPacked) * 100
    }

    private var formattedTotal: String {
        guard let est = state.estimate else { return "—" }
        let wallCents = est.wallMaterial.pricePerBoxCents.map { $0 * est.wallBoxesTotal } ?? 0
        let floorCents = est.floorMaterial.pricePerBoxCents.map { $0 * est.floorBoxesTotal } ?? 0
        let total = (wallCents + floorCents) / 100
        return total > 0 ? "$\(total)" : "—"
    }

    // MARK: - network

    private func setPolicy(overage: OveragePolicy, tier: ContractorTier?, start: StartingPointRule) async {
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let body = SetTilePoliciesRequest(
                overagePolicy: overage,
                contractorTier: overage == .contractorTier ? (tier ?? .pro) : nil,
                startingPointRule: start
            )
            try await client.setPolicies(projectId: projectId, body: body)
            let fresh = try await client.getState(projectId: projectId)
            onStateChange(fresh)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func requestRender() async {
        confirmingRender = true
        defer { confirmingRender = false }
        do {
            try await client.requestRender(projectId: projectId)
            let fresh = try await client.getState(projectId: projectId)
            onStateChange(fresh)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
