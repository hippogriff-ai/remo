import Foundation
import Observation
import OSLog

private let logger = Logger(subsystem: "com.remo.app", category: "ProjectState")

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

    /// Convenience for previews and tests — create a state at a given step with sample data.
    public static func preview(step: ProjectStep = .photoUpload, projectId: String = "preview-1") -> ProjectState {
        let state = ProjectState()
        state.projectId = projectId
        state.step = step
        switch step {
        case .photoUpload:
            break
        case .scan:
            state.photos = [
                PhotoData(photoId: "r1", storageKey: "photos/room_0.jpg", photoType: "room", note: nil),
                PhotoData(photoId: "r2", storageKey: "photos/room_1.jpg", photoType: "room", note: nil),
            ]
        case .intake:
            state.photos = [
                PhotoData(photoId: "r1", storageKey: "photos/room_0.jpg", photoType: "room", note: nil),
                PhotoData(photoId: "r2", storageKey: "photos/room_1.jpg", photoType: "room", note: nil),
            ]
        case .generation:
            break
        case .selection:
            state.generatedOptions = [
                DesignOption(imageUrl: "https://placehold.co/800x600/e8d5b7/333?text=Modern+Minimalist", caption: "Modern Minimalist"),
                DesignOption(imageUrl: "https://placehold.co/800x600/b7d5e8/333?text=Warm+Contemporary", caption: "Warm Contemporary"),
            ]
        case .iteration:
            state.currentImage = "https://placehold.co/800x600/e8d5b7/333?text=Current+Design"
            state.iterationCount = 1
        case .approval:
            state.currentImage = "https://placehold.co/800x600/e8d5b7/333?text=Final+Design"
            state.iterationCount = 2
        case .shopping:
            break
        case .completed:
            state.currentImage = "https://placehold.co/800x600/e8d5b7/333?text=Completed+Design"
            state.iterationCount = 2
            state.revisionHistory = [
                RevisionRecord(revisionNumber: 1, type: "annotation", baseImageUrl: "https://example.com/base.png", revisedImageUrl: "https://example.com/rev1.png", instructions: ["Replace lamp with modern floor lamp"]),
            ]
            state.shoppingList = ShoppingListOutput(items: [
                ProductMatch(categoryGroup: "Furniture", productName: "Accent Chair", retailer: "West Elm", priceCents: 24999, productUrl: "https://example.com/chair", imageUrl: nil, confidenceScore: 0.92, whyMatched: "Modern minimalist style", fitStatus: "fits", fitDetail: "Fits through doorway", dimensions: "32\"W x 28\"D x 31\"H"),
                ProductMatch(categoryGroup: "Lighting", productName: "Floor Lamp", retailer: "CB2", priceCents: 8999, productUrl: "https://example.com/lamp", imageUrl: nil, confidenceScore: 0.85, whyMatched: "Warm ambient lighting", fitStatus: nil, fitDetail: nil, dimensions: nil),
            ], unmatched: [
                UnmatchedItem(category: "Rug", searchKeywords: "modern geometric rug 5x7", googleShoppingUrl: "https://www.google.com/search?tbm=shop&q=modern+geometric+rug+5x7"),
            ], totalEstimatedCostCents: 33998)
        }
        return state
    }

    /// Update from a WorkflowState response (polling result).
    public func apply(_ state: WorkflowState) {
        if let newStep = ProjectStep(rawValue: state.step) {
            self.step = newStep
        } else {
            logger.warning("Unknown workflow step from backend: '\(state.step)' — keeping current step '\(self.step.rawValue)'")
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
        photos.filter { $0.photoTypeEnum == .room }.count
    }

    /// Inspiration photo count (max 3).
    public var inspirationPhotoCount: Int {
        photos.filter { $0.photoTypeEnum == .inspiration }.count
    }
}
