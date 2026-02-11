import SwiftUI
import RemoModels

/// Maps ProjectStep to the correct destination view.
/// Used inside NavigationStack to drive the flow.
struct ProjectRouter: View {
    let step: ProjectStep
    let projectState: ProjectState
    let client: any WorkflowClientProtocol

    var body: some View {
        switch step {
        case .photoUpload:
            PhotoUploadScreen(projectState: projectState, client: client)
        case .scan:
            LiDARScanScreen(projectState: projectState, client: client)
        case .intake:
            IntakeChatScreen(projectState: projectState, client: client)
        case .generation:
            GeneratingScreen(projectState: projectState)
        case .selection:
            DesignSelectionScreen(projectState: projectState, client: client)
        case .iteration:
            IterationScreen(projectState: projectState, client: client)
        case .approval:
            ApprovalScreen(projectState: projectState, client: client)
        case .shopping:
            ShoppingListScreen(projectState: projectState)
        case .completed:
            OutputScreen(projectState: projectState, client: client)
        }
    }
}
