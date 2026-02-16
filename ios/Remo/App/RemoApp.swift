import SwiftUI
import RemoModels
import RemoNetworking
import os.log

private let appLog = Logger(subsystem: "com.hippogriff.remo", category: "App")

@main
struct RemoApp: App {
    private let client: any WorkflowClientProtocol

    init() {
        let isMaestroTest = UserDefaults.standard.bool(forKey: "maestro-test")

        // Priority: 1) UserDefaults (runtime override)  2) Info.plist/xcconfig (build-time)
        //           3) Scheme env var (simulator only)   4) Mock fallback
        let resolvedURL: URL? = {
            if let ud = UserDefaults.standard.string(forKey: "backend-url"),
               let url = URL(string: ud) {
                appLog.info("Backend URL from UserDefaults: \(ud)")
                return url
            }
            let plistRaw = Bundle.main.object(forInfoDictionaryKey: "BackendURL")
            appLog.info("BackendURL plist raw value: \(String(describing: plistRaw))")
            if let plist = plistRaw as? String, !plist.isEmpty,
               let url = URL(string: plist) {
                appLog.info("Backend URL from Info.plist: \(plist)")
                return url
            }
            #if DEBUG
            if let env = ProcessInfo.processInfo.environment["BACKEND_URL"],
               let url = URL(string: env) {
                appLog.info("Backend URL from env var: \(env)")
                return url
            }
            #endif
            appLog.warning("No backend URL found — falling back to mock")
            return nil
        }()

        if !isMaestroTest, let url = resolvedURL {
            appLog.info("Using RealWorkflowClient → \(url)")
            client = RealWorkflowClient(baseURL: url)
        } else {
            appLog.warning("Using MockWorkflowClient (maestro=\(isMaestroTest), url=\(String(describing: resolvedURL)))")
            client = MockWorkflowClient(skipPhotos: isMaestroTest)
        }
    }

    var body: some Scene {
        WindowGroup {
            HomeScreen(client: client)
        }
    }
}
