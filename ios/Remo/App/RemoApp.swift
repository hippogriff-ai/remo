import SwiftUI
import RemoModels
import RemoNetworking

@main
struct RemoApp: App {
    private let client: any WorkflowClientProtocol

    init() {
        let isMaestroTest = UserDefaults.standard.bool(forKey: "maestro-test")

        // Priority: 1) UserDefaults (runtime override)  2) Info.plist/xcconfig (build-time)
        //           3) Scheme env var (simulator only)   4) Mock fallback
        let resolvedURL: URL? = {
            if let ud = UserDefaults.standard.string(forKey: "backend-url"),
               let url = URL(string: ud) { return url }
            if let plist = Bundle.main.object(forInfoDictionaryKey: "BackendURL") as? String,
               !plist.isEmpty,
               let url = URL(string: plist) { return url }
            #if DEBUG
            if let env = ProcessInfo.processInfo.environment["BACKEND_URL"],
               let url = URL(string: env) { return url }
            #endif
            return nil
        }()

        let isValidBackend = resolvedURL.flatMap { url in
            guard let scheme = url.scheme, ["http", "https"].contains(scheme),
                  url.host != nil else { return nil as URL? }
            return url
        }

        if !isMaestroTest, let url = isValidBackend {
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
