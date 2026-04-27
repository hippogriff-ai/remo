import Foundation
import SwiftUI

/// Public entry point for the tile-mode package. Consumers instantiate
/// `TileProjectFlowScreen` with a project ID + client.
public enum TileMode {
    public static let tileProjectIdsKey = "remo_tile_project_ids"
}

/// Stepper shown across tile screens.
enum TilePhase: String, CaseIterable {
    case scan, specs, estimate, render, export
    var displayName: String {
        switch self {
        case .scan: return "Scan"
        case .specs: return "Tile specs"
        case .estimate: return "Estimate"
        case .render: return "Preview"
        case .export: return "Export"
        }
    }
}

struct TileStepperBar: View {
    let current: TilePhase
    var body: some View {
        HStack(spacing: 6) {
            ForEach(Array(TilePhase.allCases.enumerated()), id: \.element) { idx, phase in
                Text(phase.displayName)
                    .font(.footnote.weight(phase == current ? .semibold : .regular))
                    .foregroundStyle(
                        phase == current ? Color.primary :
                            idx < (TilePhase.allCases.firstIndex(of: current) ?? 0) ?
                            Color.accentColor : Color.secondary
                    )
                if idx < TilePhase.allCases.count - 1 {
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
    }
}
