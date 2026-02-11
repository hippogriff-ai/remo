import SwiftUI
import RemoModels

/// Shopping list: products grouped by category, confidence badges, fit status, buy links.
public struct ShoppingListScreen: View {
    @Bindable var projectState: ProjectState

    public init(projectState: ProjectState) {
        self.projectState = projectState
    }

    public var body: some View {
        Group {
            if let shopping = projectState.shoppingList {
                ShoppingContent(shopping: shopping)
            } else {
                ContentUnavailableView(
                    "No Shopping List",
                    systemImage: "cart",
                    description: Text("Approve your design to generate a shopping list.")
                )
            }
        }
        .navigationTitle("Shopping List")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Shopping Content

struct ShoppingContent: View {
    let shopping: ShoppingListOutput

    private var groupedItems: [(category: String, items: [ProductMatch])] {
        Dictionary(grouping: shopping.items, by: \.categoryGroup)
            .sorted { $0.key < $1.key }
            .map { (category: $0.key, items: $0.value) }
    }

    var body: some View {
        List {
            // Total cost
            Section {
                HStack {
                    Text("Estimated Total")
                        .font(.headline)
                    Spacer()
                    Text(formatPrice(shopping.totalEstimatedCostCents))
                        .font(.title3.bold())
                        .foregroundStyle(.primary)
                }
            }

            // Grouped products
            ForEach(groupedItems, id: \.category) { group in
                Section(group.category) {
                    ForEach(group.items, id: \.productName) { item in
                        ProductCard(item: item)
                    }
                }
            }

            // Unmatched items
            if !shopping.unmatched.isEmpty {
                Section("Couldn't Find Exact Matches") {
                    ForEach(shopping.unmatched, id: \.category) { item in
                        UnmatchedCard(item: item)
                    }
                }
            }
        }
    }

    private func formatPrice(_ cents: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        return formatter.string(from: NSNumber(value: Double(cents) / 100.0)) ?? "$\(cents / 100)"
    }
}

// MARK: - Product Card

struct ProductCard: View {
    let item: ProductMatch

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                // Image placeholder
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.secondary.opacity(0.1))
                    .frame(width: 64, height: 64)
                    .overlay {
                        Image(systemName: "bag")
                            .foregroundStyle(.secondary)
                    }

                VStack(alignment: .leading, spacing: 4) {
                    Text(item.productName)
                        .font(.subheadline.bold())
                    Text(item.retailer)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(formatPrice(item.priceCents))
                        .font(.subheadline.bold())
                        .foregroundStyle(.primary)
                }

                Spacer()

                // Confidence badge
                ConfidenceBadge(score: item.confidenceScore)
            }

            // Fit status
            if let fitStatus = item.fitStatus {
                FitBadge(status: fitStatus, detail: item.fitDetail)
            }

            // Dimensions
            if let dimensions = item.dimensions {
                Text(dimensions)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // Buy link
            if let url = URL(string: item.productUrl) {
                Link(destination: url) {
                    Label("Buy", systemImage: "arrow.up.right.square")
                        .font(.subheadline)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private func formatPrice(_ cents: Int) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        return formatter.string(from: NSNumber(value: Double(cents) / 100.0)) ?? "$\(cents / 100)"
    }
}

// MARK: - Confidence Badge

struct ConfidenceBadge: View {
    let score: Double

    var body: some View {
        Text(label)
            .font(.caption2.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }

    private var label: String {
        score >= 0.8 ? "Match" : "Close"
    }

    private var color: Color {
        score >= 0.8 ? .green : .orange
    }
}

// MARK: - Fit Badge

struct FitBadge: View {
    let status: String
    let detail: String?

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: status == "fits" ? "checkmark.circle" : "exclamationmark.triangle")
                .font(.caption2)
            Text(status == "fits" ? "Fits your room" : "Tight fit")
                .font(.caption)
            if let detail {
                Text("- \(detail)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .foregroundStyle(status == "fits" ? .green : .orange)
    }
}

// MARK: - Unmatched Card

struct UnmatchedCard: View {
    let item: UnmatchedItem

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(item.category)
                .font(.subheadline.bold())
            Text("Search: \(item.searchKeywords)")
                .font(.caption)
                .foregroundStyle(.secondary)
            if let url = URL(string: item.googleShoppingUrl) {
                Link(destination: url) {
                    Label("Search Google Shopping", systemImage: "magnifyingglass")
                        .font(.subheadline)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
