import Foundation
import Observation

/// Central observable state for a single design project.
/// Views observe this; the poller updates it from WorkflowState responses.
@Observable
public final class ProjectState {
    public var projectId: String?
    public var step: ProjectStep = .photoUpload
    public var generationStatus: GenerationStatus = .idle

    // Workflow data
    public var photos: [PhotoData] = []
    public var scanData: ScanData?
    public var designBrief: DesignBrief?
    public var generatedOptions: [DesignOption] = []
    public var selectedOption: Int?
    public var currentImage: String?
    public var revisionHistory: [RevisionRecord] = []
    public var iterationCount: Int = 0
    public var shoppingList: ShoppingListOutput?
    public var approved: Bool = false
    public var error: WorkflowError?
    public var chatHistoryKey: String?

    // Intake conversation
    public var chatMessages: [ChatMessage] = []
    public var currentIntakeOutput: IntakeChatOutput?

    public init() {}

    /// Update from a WorkflowState response (polling result).
    public func apply(_ state: WorkflowState) {
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
    public var roomPhotoCount: Int {
        photos.filter { $0.photoType == "room" }.count
    }

    /// Inspiration photo count (max 3).
    public var inspirationPhotoCount: Int {
        photos.filter { $0.photoType == "inspiration" }.count
    }
}
