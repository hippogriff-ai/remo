import SwiftUI
import RemoModels
import RemoPhotoUpload
import RemoChatUI
import RemoAnnotation
import RemoDesignViews
import RemoShoppingList
import RemoLiDAR

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
            GeneratingScreen(projectState: projectState, client: client)
        case .selection:
            DesignSelectionScreen(projectState: projectState, client: client)
        case .iteration:
            IterationScreen(projectState: projectState, client: client)
        case .approval:
            ApprovalScreen(projectState: projectState, client: client)
        case .shopping:
            ShoppingGeneratingScreen(projectState: projectState, client: client)
        case .completed:
            OutputScreen(projectState: projectState, client: client)
        }
    }
}
