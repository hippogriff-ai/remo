import Foundation
import SwiftUI

enum TileUnits: String, CaseIterable {
    case metric, imperial
    var displayName: String { self == .metric ? "Metric" : "Imperial" }
    var lengthSuffix: String { self == .metric ? "cm" : "in" }
}

/// Manual tile specs form with metric / imperial toggle. Values are always
/// stored in cm internally (matching the backend contract); the UI layer
/// does the conversion on display + edit.
struct TileSpecsScreen: View {
    let projectId: String
    let client: any TileWorkflowClient
    let onAdvance: () -> Void
    let onBack: () -> Void

    @State private var units: TileUnits = .metric
    @State private var wall = TileSpecInput(widthCm: 30, heightCm: 60, groutMm: 3, perBox: 10, pricePerBox: 42.0)
    @State private var floor = TileSpecInput(widthCm: 60, heightCm: 60, groutMm: 3, perBox: 5, pricePerBox: 68.0)
    @State private var isSubmitting = false
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                TileStepperBar(current: .specs)

                HStack {
                    Button(action: onBack) {
                        Label("Back to scan", systemImage: "chevron.left")
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(Color.accentColor)
                    }
                    .accessibilityIdentifier("tile_specs_back")
                    Spacer()
                }
                .padding(.horizontal, 20)

                HStack {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Tile Specs")
                            .font(.title2.weight(.bold))
                    }
                    Spacer()
                    Picker("Units", selection: $units) {
                        ForEach(TileUnits.allCases, id: \.self) { u in
                            Text(u.displayName).tag(u)
                        }
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 180)
                    .accessibilityIdentifier("tile_units_picker")
                }
                .padding(.horizontal, 20)

                Text("Enter the wall and floor tile dimensions from the box. Grout and tiles-per-box can change the estimate — fill them in accurately.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 20)

                TileSpecCard(title: "Wall tile", iconTint: .blue, spec: $wall, units: units, fieldPrefix: "tile_wall")
                TileSpecCard(title: "Floor tile", iconTint: .orange, spec: $floor, units: units, fieldPrefix: "tile_floor")

                if let errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal, 20)
                }

                Button {
                    Task { await submit() }
                } label: {
                    HStack {
                        if isSubmitting { ProgressView().tint(.white) }
                        Text(isSubmitting ? "Calculating…" : "See Estimate")
                            .font(.headline)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(Color.accentColor)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .disabled(isSubmitting || !isValid)
                .opacity(isValid ? 1 : 0.5)
                .padding(.horizontal, 20)
                .accessibilityIdentifier("tile_specs_continue")

                Spacer(minLength: 40)
            }
            .padding(.top, 16)
        }
    }

    private var isValid: Bool {
        wall.widthCm > 0 && wall.heightCm > 0 && wall.perBox > 0
            && floor.widthCm > 0 && floor.heightCm > 0 && floor.perBox > 0
    }

    private func submit() async {
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            try await client.setMaterials(projectId: projectId, wall: wall, floor: floor)
            onAdvance()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct TileSpecCard: View {
    let title: String
    let iconTint: Color
    @Binding var spec: TileSpecInput
    let units: TileUnits
    let fieldPrefix: String

    private var widthInDisplayUnit: Binding<Double> { lengthBinding(\.widthCm) }
    private var heightInDisplayUnit: Binding<Double> { lengthBinding(\.heightCm) }

    private func lengthBinding(_ keyPath: WritableKeyPath<TileSpecInput, Double>) -> Binding<Double> {
        Binding(
            get: {
                let cm = spec[keyPath: keyPath]
                return units == .metric ? cm : cm / 2.54
            },
            set: { newValue in
                spec[keyPath: keyPath] = units == .metric ? newValue : newValue * 2.54
            }
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                RoundedRectangle(cornerRadius: 8)
                    .fill(iconTint.opacity(0.2))
                    .frame(width: 44, height: 44)
                    .overlay {
                        Image(systemName: "square.grid.3x3.fill")
                            .foregroundStyle(iconTint)
                    }
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline)
                    Text(summaryLine)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                LengthField(
                    label: "Width (\(units.lengthSuffix))",
                    value: widthInDisplayUnit,
                    id: "\(fieldPrefix)_width"
                )
                LengthField(
                    label: "Height (\(units.lengthSuffix))",
                    value: heightInDisplayUnit,
                    id: "\(fieldPrefix)_height"
                )
                LengthField(
                    label: "Grout (mm)",
                    value: $spec.groutMm,
                    id: "\(fieldPrefix)_grout"
                )
                IntField(
                    label: "Per box",
                    value: $spec.perBox,
                    id: "\(fieldPrefix)_per_box"
                )
            }

            PriceField(price: Binding(
                get: { spec.pricePerBox ?? 0 },
                set: { spec.pricePerBox = $0 == 0 ? nil : $0 }
            ), id: "\(fieldPrefix)_price")
        }
        .padding(16)
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .padding(.horizontal, 20)
    }

    private var summaryLine: String {
        let w = units == .metric ? spec.widthCm : spec.widthCm / 2.54
        let h = units == .metric ? spec.heightCm : spec.heightCm / 2.54
        let wText = units == .metric ? String(format: "%.0f", w) : String(format: "%.1f", w)
        let hText = units == .metric ? String(format: "%.0f", h) : String(format: "%.1f", h)
        return "\(wText)×\(hText) \(units.lengthSuffix) · \(Int(spec.groutMm))mm grout · \(spec.perBox)/box"
    }
}

private struct LengthField: View {
    let label: String
    @Binding var value: Double
    let id: String
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            TextField(label, value: $value, format: .number.precision(.fractionLength(0...2)))
                .keyboardType(.decimalPad)
                .padding(10)
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .accessibilityIdentifier(id)
        }
    }
}

private struct IntField: View {
    let label: String
    @Binding var value: Int
    let id: String
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            TextField(label, value: $value, format: .number)
                .keyboardType(.numberPad)
                .padding(10)
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .accessibilityIdentifier(id)
        }
    }
}

private struct PriceField: View {
    @Binding var price: Double
    let id: String
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Price per box ($)").font(.caption).foregroundStyle(.secondary)
            TextField("Price (optional)", value: $price, format: .number)
                .keyboardType(.decimalPad)
                .padding(10)
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .accessibilityIdentifier(id)
        }
    }
}
