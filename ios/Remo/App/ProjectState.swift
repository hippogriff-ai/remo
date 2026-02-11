import Foundation
import RemoModels

/// Central observable state for a single design project.
/// Views observe this; the poller updates it from WorkflowState responses.
@Observable
final class ProjectState {
    var projectId: String?
    var step: ProjectStep = .photoUpload
    var generationStatus: GenerationStatus = .idle

    // Workflow data
    var photos: [PhotoData] = []
    var scanData: ScanData?
    var designBrief: DesignBrief?
    var generatedOptions: [DesignOption] = []
    var selectedOption: Int?
    var currentImage: String?
    var revisionHistory: [RevisionRecord] = []
    var iterationCount: Int = 0
    var shoppingList: ShoppingListOutput?
    var approved: Bool = false
    var error: WorkflowError?
    var chatHistoryKey: String?

    // Intake conversation
    var chatMessages: [ChatMessage] = []
    var currentIntakeOutput: IntakeChatOutput?

    /// Update from a WorkflowState response (polling result).
    func apply(_ state: WorkflowState) {
        if let newStep = ProjectStep(rawValue: state.step) {
            self.step = newStep
        }
        self.photos = state.photos
        self.scanData = state.scanData
        self.designBrief = state.designBrief
        self.generatedOptions = state.generatedOptions
        self.selectedOption = state.selectedOption
        self.currentImage = state.currentImage
        self.revisionHistory = state.revisionHistory
        self.iterationCount = state.iterationCount
        self.shoppingList = state.shoppingList
        self.approved = state.approved
        self.error = state.error
        self.chatHistoryKey = state.chatHistoryKey
    }

    /// Room photo count for enforcing the 2-photo minimum.
    var roomPhotoCount: Int {
        photos.filter { $0.photoType == "room" }.count
    }

    /// Inspiration photo count (max 3).
    var inspirationPhotoCount: Int {
        photos.filter { $0.photoType == "inspiration" }.count
    }
}
