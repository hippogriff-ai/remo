import XCTest
@testable import RemoNetworking
import RemoModels

final class MockClientTests: XCTestCase {
    var client: MockWorkflowClient!

    override func setUp() {
        client = MockWorkflowClient(delay: .zero) // No delay for tests
    }

    // MARK: - Project lifecycle

    func testCreateProjectReturnsId() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        XCTAssertFalse(id.isEmpty)
    }

    func testGetStateReturnsPhotosStep() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "photos")
    }

    func testGetStateNotFoundThrows() async {
        do {
            _ = try await client.getState(projectId: "nonexistent")
            XCTFail("Should have thrown")
        } catch let error as APIError {
            if case .httpError(let code, let response) = error {
                XCTAssertEqual(code, 404)
                XCTAssertEqual(response.error, "workflow_not_found")
                XCTAssertFalse(response.retryable)
            } else {
                XCTFail("Expected httpError, got \(error)")
            }
        } catch {
            XCTFail("Expected APIError, got \(error)")
        }
    }

    func testDeleteProject() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        try await client.deleteProject(projectId: id)
        do {
            _ = try await client.getState(projectId: id)
            XCTFail("Should have thrown after delete")
        } catch let error as APIError {
            if case .httpError(let code, _) = error {
                XCTAssertEqual(code, 404)
            } else {
                XCTFail("Expected httpError, got \(error)")
            }
        } catch {
            XCTFail("Expected APIError, got \(error)")
        }
    }

    func testUploadPhotoNotFoundThrows() async {
        do {
            _ = try await client.uploadPhoto(projectId: "nonexistent", imageData: Data(), photoType: "room")
            XCTFail("Should have thrown")
        } catch let error as APIError {
            if case .httpError(let code, _) = error {
                XCTAssertEqual(code, 404)
            } else {
                XCTFail("Expected httpError, got \(error)")
            }
        } catch {
            XCTFail("Expected APIError, got \(error)")
        }
    }

    // MARK: - Photo upload

    func testPhotoUploadReturnsValidation() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        let response = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        XCTAssertTrue(response.validation.passed)
    }

    func testTwoRoomPhotosTransitionToScan() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "scan")
        XCTAssertEqual(state.photos.count, 2)
    }

    // MARK: - Scan

    func testSkipScanTransitionsToIntake() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        try await client.skipScan(projectId: id)
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "intake")
    }

    func testUploadScanTransitionsToIntake() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: true)
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        try await client.uploadScan(projectId: id, scanData: [
            "room": ["width": 4.0, "length": 5.0, "height": 2.5, "unit": "meters"] as [String: Any],
        ])
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "intake")
        XCTAssertNotNil(state.scanData)
    }

    // MARK: - Intake

    func testIntakeConversationFlow() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        try await client.skipScan(projectId: id)

        let start = try await client.startIntake(projectId: id, mode: "full")
        XCTAssertFalse(start.agentMessage.isEmpty)
        XCTAssertNotNil(start.options)
        XCTAssertEqual(start.progress, "Question 1 of 3")

        let msg1 = try await client.sendIntakeMessage(projectId: id, message: "living room")
        XCTAssertNotNil(msg1.options)
        XCTAssertEqual(msg1.progress, "Question 2 of 3")

        let msg2 = try await client.sendIntakeMessage(projectId: id, message: "modern")
        XCTAssertTrue(msg2.isOpenEnded)

        let msg3 = try await client.sendIntakeMessage(projectId: id, message: "replace the couch")
        XCTAssertTrue(msg3.isSummary)
        XCTAssertNotNil(msg3.partialBrief)
    }

    func testConfirmIntakeTransitionsToSelection() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        try await client.skipScan(projectId: id)

        let brief = DesignBrief(roomType: "living room")
        try await client.confirmIntake(projectId: id, brief: brief)
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "selection")
        XCTAssertEqual(state.generatedOptions.count, 2)
    }

    func testSkipIntakeTransitionsToSelection() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        try await client.skipScan(projectId: id)
        try await client.skipIntake(projectId: id)
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "selection")
    }

    // MARK: - Selection & Iteration

    func testSelectOptionTransitionsToIteration() async throws {
        let id = try await setupToSelection()
        try await client.selectOption(projectId: id, index: 0)
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "iteration")
        XCTAssertEqual(state.selectedOption, 0)
        XCTAssertNotNil(state.currentImage)
    }

    func testAnnotationEditIncrementsIteration() async throws {
        let id = try await setupToIteration()
        let annotation = AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.5, radius: 0.1, instruction: "Replace this with a modern lamp please")
        try await client.submitAnnotationEdit(projectId: id, annotations: [annotation])
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.iterationCount, 1)
        XCTAssertEqual(state.revisionHistory.count, 1)
        XCTAssertEqual(state.revisionHistory[0].type, "annotation")
    }

    func testTextFeedbackIncrementsIteration() async throws {
        let id = try await setupToIteration()
        try await client.submitTextFeedback(projectId: id, feedback: "Make the room brighter")
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.iterationCount, 1)
        XCTAssertEqual(state.revisionHistory[0].type, "feedback")
    }

    func testFiveIterationsForcesApproval() async throws {
        let id = try await setupToIteration()
        let annotation = AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.5, radius: 0.1, instruction: "Replace this with a modern lamp please")

        // Iterations 1-4 should keep step as "iteration"
        for i in 1...4 {
            try await client.submitAnnotationEdit(projectId: id, annotations: [annotation])
            let state = try await client.getState(projectId: id)
            XCTAssertEqual(state.iterationCount, i)
            XCTAssertEqual(state.step, "iteration", "Iteration \(i) should still be 'iteration'")
        }

        // Iteration 5 should force transition to "approval"
        try await client.submitAnnotationEdit(projectId: id, annotations: [annotation])
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.iterationCount, 5)
        XCTAssertEqual(state.step, "approval")
    }

    // MARK: - Approval

    func testApproveDesignTransitionsToCompleted() async throws {
        let id = try await setupToIteration()
        try await client.approveDesign(projectId: id)
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "completed")
        XCTAssertTrue(state.approved)
        XCTAssertNotNil(state.shoppingList)
        XCTAssertFalse(state.shoppingList!.items.isEmpty)
    }

    // MARK: - Start Over

    func testStartOverResetsToIntake() async throws {
        let id = try await setupToIteration()
        try await client.startOver(projectId: id)
        let state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "intake")
        XCTAssertTrue(state.generatedOptions.isEmpty)
        XCTAssertNil(state.selectedOption)
        XCTAssertEqual(state.iterationCount, 0)
    }

    // MARK: - Full flow

    func testFullFlowPhotosToCompleted() async throws {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)

        // Upload 2 room photos -> scan step
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        var state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "scan")

        // Skip scan -> intake
        try await client.skipScan(projectId: id)
        state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "intake")

        // Skip intake -> selection
        try await client.skipIntake(projectId: id)
        state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "selection")

        // Select option 0 -> iteration
        try await client.selectOption(projectId: id, index: 0)
        state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "iteration")

        // Approve -> completed
        try await client.approveDesign(projectId: id)
        state = try await client.getState(projectId: id)
        XCTAssertEqual(state.step, "completed")
        XCTAssertTrue(state.approved)
        XCTAssertNotNil(state.shoppingList)
    }

    // MARK: - Helpers

    private func setupToSelection() async throws -> String {
        let id = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: id, imageData: Data(), photoType: "room")
        try await client.skipScan(projectId: id)
        try await client.skipIntake(projectId: id)
        return id
    }

    private func setupToIteration() async throws -> String {
        let id = try await setupToSelection()
        try await client.selectOption(projectId: id, index: 0)
        return id
    }
}
