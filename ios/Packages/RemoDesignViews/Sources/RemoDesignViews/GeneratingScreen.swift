import SwiftUI
import RemoModels

/// Loading screen shown while designs are being generated.
public struct GeneratingScreen: View {
    @Bindable var projectState: ProjectState

    public init(projectState: ProjectState) {
        self.projectState = projectState
    }

    public var body: some View {
        VStack(spacing: 24) {
            Spacer()

            ProgressView()
                .scaleEffect(1.5)
                .padding()

            Text("Creating your designs...")
                .font(.title3.bold())

            Text("This usually takes 15-30 seconds.\nWe're generating 2 unique options for you.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()
        }
        .padding()
        .navigationTitle("Generating")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden()
        #endif
    }
}
