import SwiftUI
import RemoModels
import RemoNetworking

@main
struct RemoApp: App {
    private let client: any WorkflowClientProtocol

    init() {
        let isMaestroTest = UserDefaults.standard.bool(forKey: "maestro-test")
        let useRealBackend = UserDefaults.standard.bool(forKey: "real-backend")
        let backendURL = UserDefaults.standard.string(forKey: "backend-url")

        if useRealBackend, let urlString = backendURL, let url = URL(string: urlString) {
            client = RealWorkflowClient(baseURL: url)
        } else {
            client = MockWorkflowClient(skipPhotos: isMaestroTest)
        }
    }

    var body: some Scene {
        WindowGroup {
            HomeScreen(client: client)
        }
    }
}
