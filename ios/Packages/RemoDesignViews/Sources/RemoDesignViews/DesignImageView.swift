import SwiftUI

/// Reusable async image loader for design option/revision images.
/// Shows a placeholder while loading, error state on failure.
struct DesignImageView: View {
    let url: String?
    let cornerRadius: CGFloat

    init(_ url: String?, cornerRadius: CGFloat = 12) {
        self.url = url
        self.cornerRadius = cornerRadius
    }

    var body: some View {
        if let url, let imageURL = URL(string: url) {
            AsyncImage(url: imageURL) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .scaledToFill()
                case .failure:
                    placeholder(icon: "exclamationmark.triangle", text: "Failed to load")
                case .empty:
                    ZStack {
                        Color.secondary.opacity(0.1)
                        ProgressView()
                    }
                @unknown default:
                    placeholder(icon: "photo", text: nil)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        } else {
            placeholder(icon: "photo.artframe", text: "No image")
        }
    }

    private func placeholder(icon: String, text: String?) -> some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(Color.secondary.opacity(0.1))
            .overlay {
                VStack(spacing: 8) {
                    Image(systemName: icon)
                        .font(.largeTitle)
                        .foregroundStyle(.secondary)
                    if let text {
                        Text(text)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
    }
}
