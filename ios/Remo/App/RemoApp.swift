import SwiftUI
import RemoModels
import RemoNetworking

@main
struct RemoApp: App {
    private let client: any WorkflowClientProtocol

    init() {
        let isMaestroTest = UserDefaults.standard.bool(forKey: "maestro-test")
        // Swap to RealWorkflowClient in P2
        client = MockWorkflowClient(skipPhotos: isMaestroTest)
    }

    var body: some Scene {
        WindowGroup {
            HomeScreen(client: client)
        }
    }
}
