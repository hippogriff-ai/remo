import SwiftUI
#if os(iOS)
import UIKit
#endif
import RemoModels
import RemoNetworking

// MARK: - Price Formatting

private let priceFormatter: NumberFormatter = {
    let f = NumberFormatter()
    f.numberStyle = .currency
    f.currencyCode = "USD"
    return f
}()

private func formatPrice(_ cents: Int) -> String {
    priceFormatter.string(from: NSNumber(value: Double(cents) / 100.0)) ?? "$\(cents / 100)"
}

/// Shopping list: products grouped by category, confidence badges, fit status, buy links.
public struct ShoppingListScreen: View {
    @Bindable var projectState: ProjectState

    @State private var showCopiedToast = false
    @State private var showShareSheet = false

    public init(projectState: ProjectState) {
        self.projectState = projectState
    }

    public var body: some View {
        Group {
            if let shopping = projectState.shoppingList {
                ShoppingContent(shopping: shopping, hasScanData: projectState.scanData != nil)
            } else {
                ContentUnavailableView(
                    "No Shopping List",
                    systemImage: "cart",
                    description: Text("Approve your design to generate a shopping list.")
                )
            }
        }
        .navigationTitle("Shopping List")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            if projectState.shoppingList != nil {
                ToolbarItemGroup(placement: .primaryAction) {
                    Button {
                        copyShoppingList()
                    } label: {
                        Label("Copy All", systemImage: "doc.on.doc")
                    }
                    .accessibilityIdentifier("shopping_copy_all")

                    #if os(iOS)
                    Button {
                        showShareSheet = true
                    } label: {
                        Label("Share", systemImage: "square.and.arrow.up")
                    }
                    .accessibilityIdentifier("shopping_share")
                    #endif
                }
            }
        }
        .overlay(alignment: .bottom) {
            if showCopiedToast {
                Text("Shopping list copied!")
                    .font(.subheadline.bold())
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(.ultraThinMaterial)
                    .clipShape(Capsule())
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .padding(.bottom, 20)
            }
        }
        #if os(iOS)
        .sheet(isPresented: $showShareSheet) {
            if let text = formattedShoppingList() {
                ShareSheet(items: [text])
            }
        }
        #endif
    }

    private func formattedShoppingList() -> String? {
        guard let shopping = projectState.shoppingList else { return nil }
        var lines: [String] = ["Remo Shopping List", ""]
        for item in shopping.items {
            let price = formatPrice(item.priceCents)
            lines.append("\(item.productName) - \(price)")
            lines.append("  \(item.retailer) — \(item.productUrl)")
            lines.append("")
        }
        lines.append("Total: \(formatPrice(shopping.totalEstimatedCostCents))")
        return lines.joined(separator: "\n")
    }

    private func copyShoppingList() {
        guard let text = formattedShoppingList() else { return }
        #if os(iOS)
        UIPasteboard.general.string = text
        #endif
        withAnimation { showCopiedToast = true }
        Task {
            try? await Task.sleep(for: .seconds(2))
            withAnimation { showCopiedToast = false }
        }
    }
}

#if os(iOS)
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
#endif

// MARK: - Shopping Content

struct ShoppingContent: View {
    let shopping: ShoppingListOutput
    var hasScanData: Bool = false

    private var groupedItems: [(category: String, items: [ProductMatch])] {
        Dictionary(grouping: shopping.items, by: \.categoryGroup)
            .sorted { $0.key < $1.key }
            .map { (category: $0.key, items: $0.value) }
    }

    var body: some View {
        List {
            // Non-LiDAR banner
            if !hasScanData {
                Section {
                    Label {
                        Text("We matched products by style. For size-verified recommendations, use Room Scan on an iPhone Pro next time.")
                            .font(.caption)
                    } icon: {
                        Image(systemName: "info.circle")
                    }
                    .foregroundStyle(.secondary)
                }
            }

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
                    ForEach(group.items, id: \.productUrl) { item in
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
}

// MARK: - Product Card

struct ProductCard: View {
    let item: ProductMatch

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                // Product image (falls back to bag icon when no URL or load fails)
                if let imageUrl = item.imageUrl, let url = URL(string: imageUrl) {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case .success(let image):
                            image.resizable().aspectRatio(contentMode: .fill)
                        default:
                            Color.secondary.opacity(0.1)
                                .overlay { Image(systemName: "bag").foregroundStyle(.secondary) }
                        }
                    }
                    .frame(width: 64, height: 64)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.secondary.opacity(0.1))
                        .frame(width: 64, height: 64)
                        .overlay { Image(systemName: "bag").foregroundStyle(.secondary) }
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text(item.productName)
                        .font(.subheadline.bold())
                    if !item.whyMatched.isEmpty {
                        Text(item.whyMatched)
                            .font(.caption)
                            .italic()
                            .foregroundStyle(.secondary)
                    }
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

            // Buy link + copy link
            HStack {
                if let url = URL(string: item.productUrl) {
                    Link(destination: url) {
                        Label("Buy", systemImage: "arrow.up.right.square")
                            .font(.subheadline)
                    }
                    .accessibilityLabel("Buy \(item.productName) from \(item.retailer)")
                }
                Spacer()
                Button {
                    #if os(iOS)
                    UIPasteboard.general.string = item.productUrl
                    #endif
                } label: {
                    Label("Copy Link", systemImage: "doc.on.doc")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
                .accessibilityIdentifier("copy_link_\(item.productName)")
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.productName), \(formatPrice(item.priceCents)), from \(item.retailer)")
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

#Preview {
    NavigationStack {
        ShoppingListScreen(projectState: .preview(step: .completed))
    }
}
