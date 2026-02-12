import Foundation
import RemoModels

/// Mock client for P1 development. Returns hardcoded responses with realistic delays.
/// All iOS development uses this via protocol injection until P2 swaps to RealWorkflowClient.
/// Actor provides compile-time data race protection for mutable state (states, intakeMessages).
public actor MockWorkflowClient: WorkflowClientProtocol {
    private var states: [String: WorkflowState] = [:]
    private var intakeMessages: [String: [String]] = [:]
    private let delay: Duration
    private let skipPhotos: Bool

    public init(delay: Duration = .milliseconds(300), skipPhotos: Bool = false) {
        self.delay = delay
        self.skipPhotos = skipPhotos
    }

    private func simulateDelay() async throws {
        try await Task.sleep(for: delay)
    }

    // MARK: - Project lifecycle

    public func createProject(deviceFingerprint: String, hasLidar: Bool) async throws -> String {
        try await simulateDelay()
        let id = UUID().uuidString
        if skipPhotos {
            var state = WorkflowState(step: "scan")
            state.photos = [
                PhotoData(photoId: "mock-room-1", storageKey: "projects/\(id)/photos/room_0.jpg", photoType: "room"),
                PhotoData(photoId: "mock-room-2", storageKey: "projects/\(id)/photos/room_1.jpg", photoType: "room"),
                PhotoData(photoId: "mock-inspo-1", storageKey: "projects/\(id)/photos/inspo_0.jpg", photoType: "inspiration"),
            ]
            states[id] = state
        } else {
            states[id] = WorkflowState(step: "photos")
        }
        return id
    }

    public func getState(projectId: String) async throws -> WorkflowState {
        try await simulateDelay()
        guard let state = states[projectId] else {
            throw APIError.httpError(
                statusCode: 404,
                response: ErrorResponse(error: "workflow_not_found", message: "Project not found", retryable: false)
            )
        }
        return state
    }

    public func deleteProject(projectId: String) async throws {
        try await simulateDelay()
        states.removeValue(forKey: projectId)
        intakeMessages.removeValue(forKey: projectId)
    }

    // MARK: - Photos

    public func uploadPhoto(projectId: String, imageData: Data, photoType: String) async throws -> PhotoUploadResponse {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }

        let photoId = UUID().uuidString
        let photo = PhotoData(
            photoId: photoId,
            storageKey: "projects/\(projectId)/photos/\(photoType)_\(state.photos.count).jpg",
            photoType: photoType
        )
        state.photos.append(photo)

        let roomCount = state.photos.filter { $0.photoTypeEnum == .room }.count
        if roomCount >= 2 && state.step == "photos" {
            state.step = "scan"
        }

        states[projectId] = state
        return PhotoUploadResponse(
            photoId: photoId,
            validation: ValidatePhotoOutput(passed: true)
        )
    }

    public func deletePhoto(projectId: String, photoId: String) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.photos.removeAll { $0.photoId == photoId }
        states[projectId] = state
    }

    // MARK: - Scan

    public func uploadScan(projectId: String, scanData: [String: Any]) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.scanData = RemoModels.ScanData(
            storageKey: "projects/\(projectId)/lidar/scan.json",
            roomDimensions: RoomDimensions(widthM: 4.2, lengthM: 5.8, heightM: 2.7)
        )
        state.step = "intake"
        states[projectId] = state
    }

    public func skipScan(projectId: String) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.step = "intake"
        states[projectId] = state
    }

    // MARK: - Intake

    public func startIntake(projectId: String, mode: String) async throws -> IntakeChatOutput {
        try await simulateDelay()
        guard states[projectId] != nil else { throw notFound() }
        intakeMessages[projectId] = []
        return IntakeChatOutput(
            agentMessage: "Welcome! Let's design your perfect room. What type of room are we working with?",
            options: [
                QuickReplyOption(number: 1, label: "Living Room", value: "living room"),
                QuickReplyOption(number: 2, label: "Bedroom", value: "bedroom"),
                QuickReplyOption(number: 3, label: "Home Office", value: "home office"),
            ],
            progress: "Question 1 of 3"
        )
    }

    public func sendIntakeMessage(projectId: String, message: String) async throws -> IntakeChatOutput {
        try await simulateDelay()
        guard states[projectId] != nil else { throw notFound() }
        var messages = intakeMessages[projectId] ?? []
        messages.append(message)
        intakeMessages[projectId] = messages

        let step = messages.count
        if step == 1 {
            return IntakeChatOutput(
                agentMessage: "Great, a \(message)! What design style are you drawn to?",
                options: [
                    QuickReplyOption(number: 1, label: "Modern Minimalist", value: "modern"),
                    QuickReplyOption(number: 2, label: "Warm & Cozy", value: "warm"),
                    QuickReplyOption(number: 3, label: "Industrial", value: "industrial"),
                    QuickReplyOption(number: 4, label: "Scandinavian", value: "scandinavian"),
                ],
                progress: "Question 2 of 3"
            )
        }
        if step == 2 {
            return IntakeChatOutput(
                agentMessage: "Love that style! Anything specific you'd like to change or keep in the room?",
                isOpenEnded: true,
                progress: "Question 3 of 3"
            )
        }
        let roomType = messages.first ?? "living room"
        return IntakeChatOutput(
            agentMessage: "Here's what I've gathered: a \(roomType) redesign. Does this look right?",
            progress: "Summary",
            isSummary: true,
            partialBrief: DesignBrief(roomType: roomType)
        )
    }

    public func confirmIntake(projectId: String, brief: DesignBrief) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.designBrief = brief
        state.generatedOptions = mockOptions(projectId: projectId)
        state.step = "selection"
        states[projectId] = state
    }

    public func skipIntake(projectId: String) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.generatedOptions = mockOptions(projectId: projectId)
        state.step = "selection"
        states[projectId] = state
    }

    // MARK: - Selection

    public func selectOption(projectId: String, index: Int) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        guard state.generatedOptions.indices.contains(index) else {
            throw APIError.httpError(statusCode: 422, response: ErrorResponse(
                error: "invalid_index", message: "Invalid option index: \(index)", retryable: false
            ))
        }
        state.selectedOption = index
        state.currentImage = state.generatedOptions[index].imageUrl
        state.step = "iteration"
        states[projectId] = state
    }

    // MARK: - Iteration

    public func submitAnnotationEdit(projectId: String, annotations: [AnnotationRegion]) async throws {
        try await Task.sleep(for: .seconds(1)) // Simulate generation time
        guard var state = states[projectId] else { throw notFound() }
        let revisionNum = state.iterationCount + 1
        let revisedUrl = "https://r2.example.com/projects/\(projectId)/generated/revision_\(revisionNum).png"
        state.revisionHistory.append(RevisionRecord(
            revisionNumber: revisionNum,
            type: "annotation",
            baseImageUrl: state.currentImage ?? "",
            revisedImageUrl: revisedUrl,
            instructions: annotations.map(\.instruction)
        ))
        state.currentImage = revisedUrl
        state.chatHistoryKey = "chat/\(projectId)/history.json"
        state.iterationCount = revisionNum
        if state.iterationCount >= 5 { state.step = "approval" }
        states[projectId] = state
    }

    public func submitTextFeedback(projectId: String, feedback: String) async throws {
        try await Task.sleep(for: .seconds(1))
        guard var state = states[projectId] else { throw notFound() }
        let revisionNum = state.iterationCount + 1
        let revisedUrl = "https://r2.example.com/projects/\(projectId)/generated/revision_\(revisionNum).png"
        state.revisionHistory.append(RevisionRecord(
            revisionNumber: revisionNum,
            type: "feedback",
            baseImageUrl: state.currentImage ?? "",
            revisedImageUrl: revisedUrl,
            instructions: [feedback]
        ))
        state.currentImage = revisedUrl
        state.chatHistoryKey = "chat/\(projectId)/history.json"
        state.iterationCount = revisionNum
        if state.iterationCount >= 5 { state.step = "approval" }
        states[projectId] = state
    }

    // MARK: - Approval & other

    public func approveDesign(projectId: String) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.approved = true
        state.shoppingList = mockShoppingList()
        state.step = "completed"
        states[projectId] = state
    }

    public func startOver(projectId: String) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.generatedOptions = []
        state.selectedOption = nil
        state.currentImage = nil
        state.designBrief = nil
        state.revisionHistory = []
        state.iterationCount = 0
        state.approved = false
        state.shoppingList = nil
        state.error = nil
        state.chatHistoryKey = nil
        state.step = "intake"
        states[projectId] = state
        intakeMessages.removeValue(forKey: projectId)
    }

    public func retryFailedStep(projectId: String) async throws {
        try await simulateDelay()
        guard var state = states[projectId] else { throw notFound() }
        state.error = nil
        states[projectId] = state
    }

    // MARK: - Helpers

    private func notFound() -> APIError {
        .httpError(statusCode: 404, response: ErrorResponse(
            error: "workflow_not_found", message: "Project not found", retryable: false
        ))
    }

    private func mockOptions(projectId: String) -> [DesignOption] {
        [
            DesignOption(
                imageUrl: "https://r2.example.com/projects/\(projectId)/generated/option_0.png",
                caption: "Modern Minimalist"
            ),
            DesignOption(
                imageUrl: "https://r2.example.com/projects/\(projectId)/generated/option_1.png",
                caption: "Warm Contemporary"
            ),
        ]
    }

    private func mockShoppingList() -> ShoppingListOutput {
        ShoppingListOutput(
            items: [
                ProductMatch(
                    categoryGroup: "Furniture",
                    productName: "Modern Accent Chair",
                    retailer: "West Elm",
                    priceCents: 24999,
                    productUrl: "https://example.com/accent-chair",
                    imageUrl: "https://example.com/images/accent-chair.jpg",
                    confidenceScore: 0.92,
                    whyMatched: "Matches modern minimalist style",
                    fitStatus: "fits",
                    dimensions: "32\"W x 28\"D x 31\"H"
                ),
                ProductMatch(
                    categoryGroup: "Lighting",
                    productName: "Arc Floor Lamp",
                    retailer: "CB2",
                    priceCents: 8999,
                    productUrl: "https://example.com/floor-lamp",
                    confidenceScore: 0.85,
                    whyMatched: "Complements room ambiance"
                ),
                ProductMatch(
                    categoryGroup: "Decor",
                    productName: "Geometric Wall Art Set",
                    retailer: "Etsy",
                    priceCents: 4500,
                    productUrl: "https://example.com/wall-art",
                    imageUrl: "https://example.com/images/wall-art.jpg",
                    confidenceScore: 0.78,
                    whyMatched: "Adds visual interest to minimalist space"
                ),
                ProductMatch(
                    categoryGroup: "Furniture",
                    productName: "Walnut Coffee Table",
                    retailer: "Article",
                    priceCents: 34900,
                    productUrl: "https://example.com/coffee-table",
                    imageUrl: "https://example.com/images/coffee-table.jpg",
                    confidenceScore: 0.95,
                    whyMatched: "Warm wood tone balances minimalist aesthetic",
                    fitStatus: "fits",
                    dimensions: "48\"W x 24\"D x 16\"H"
                ),
            ],
            unmatched: [
                UnmatchedItem(
                    category: "Rug",
                    searchKeywords: "modern geometric area rug 5x7",
                    googleShoppingUrl: "https://www.google.com/search?tbm=shop&q=modern+geometric+rug+5x7"
                ),
            ],
            totalEstimatedCostCents: 73398
        )
    }
}
