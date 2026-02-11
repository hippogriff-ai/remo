import SwiftUI
import RemoModels
import RemoNetworking

@main
struct RemoApp: App {
    // Swap to RealWorkflowClient in P2
    private let client: any WorkflowClientProtocol = MockWorkflowClient()

    var body: some Scene {
        WindowGroup {
            HomeScreen(client: client)
        }
    }
}
