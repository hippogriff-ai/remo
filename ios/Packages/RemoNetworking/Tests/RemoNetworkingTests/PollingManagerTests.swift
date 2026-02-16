import XCTest
@testable import RemoNetworking
import RemoModels

final class PollingManagerTests: XCTestCase {

    // MARK: - Step change detection

    func testPollReturnsWhenStepChanges() async throws {
        let client = MockWorkflowClient(delay: .zero)
        let projectId = try await client.createProject(deviceFingerprint: "test", hasLidar: false)

        // Start polling in a task, then change the step
        let poller = PollingManager(client: client, interval: .milliseconds(50))

        // Upload photos then skip scan to trigger step change from "photos" -> "intake"
        _ = try await client.uploadPhoto(projectId: projectId, imageData: Data(), photoType: "room")
        _ = try await client.uploadPhoto(projectId: projectId, imageData: Data(), photoType: "room")
        try await client.skipScan(projectId: projectId)

        let newState = try await poller.pollUntilStepChanges(projectId: projectId, currentStep: "photos")
        XCTAssertEqual(newState.step, "intake")
    }

    func testPollReturnsOnErrorState() async throws {
        let client = FlakyClient(failCount: 0, stepAfterFail: "generation")
        await client.setErrorState(WorkflowError(message: "Generation failed", retryable: true))

        let poller = PollingManager(client: client, interval: .milliseconds(50))
        let state = try await poller.pollUntilStepChanges(projectId: "test-123", currentStep: "generation")
        XCTAssertNotNil(state.error)
        XCTAssertEqual(state.error?.message, "Generation failed")
    }

    // MARK: - Retry behavior

    func testPollRetriesTransientErrors() async throws {
        // Fail twice, then succeed with a step change
        let client = FlakyClient(failCount: 2, stepAfterFail: "selection")
        let poller = PollingManager(client: client, interval: .milliseconds(50), maxRetries: 3)

        let state = try await poller.pollUntilStepChanges(projectId: "test-123", currentStep: "generation")
        XCTAssertEqual(state.step, "selection")
        let count = await client.callCount
        XCTAssertEqual(count, 3) // 2 failures + 1 success
    }

    func testPollThrowsAfterMaxRetries() async throws {
        // Fail 4 times — exceeds maxRetries of 3
        let client = FlakyClient(failCount: 10, stepAfterFail: "selection")
        let poller = PollingManager(client: client, interval: .milliseconds(50), maxRetries: 3)

        do {
            _ = try await poller.pollUntilStepChanges(projectId: "test-123", currentStep: "generation")
            XCTFail("Should have thrown after exceeding max retries")
        } catch let error as APIError {
            XCTAssertTrue(error.isRetryable)
        } catch {
            XCTFail("Expected APIError, got \(error)")
        }
        let count = await client.callCount
        XCTAssertEqual(count, 4) // maxRetries + 1
    }

    func testPollImmediatelyThrowsNonRetryableError() async throws {
        let client = FlakyClient(failCount: 10, stepAfterFail: "selection")
        await client.setErrorToThrow(APIError.httpError(
            statusCode: 404,
            response: ErrorResponse(error: "not_found", message: "Not found", retryable: false)
        ))
        let poller = PollingManager(client: client, interval: .milliseconds(50), maxRetries: 3)

        do {
            _ = try await poller.pollUntilStepChanges(projectId: "test-123", currentStep: "generation")
            XCTFail("Should have thrown immediately")
        } catch let error as APIError {
            XCTAssertFalse(error.isRetryable)
        } catch {
            XCTFail("Expected APIError, got \(error)")
        }
        let count = await client.callCount
        XCTAssertEqual(count, 1) // fails immediately, no retries
    }

    func testSinglePollReturnsState() async throws {
        let client = MockWorkflowClient(delay: .zero)
        let projectId = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        let poller = PollingManager(client: client)

        let state = try await poller.poll(projectId: projectId)
        XCTAssertEqual(state.step, "photos")
    }

    // MARK: - Cancellation

    // MARK: - pollUntil (custom condition)

    func testPollUntilReturnsWhenConditionMet() async throws {
        // Simulate iteration count incrementing: client returns iterationCount=2 on second call
        let client = IncrementingClient(initialCount: 1, targetCount: 2)
        let poller = PollingManager(client: client, interval: .milliseconds(50))

        let state = try await poller.pollUntil(projectId: "test-123") { state in
            state.iterationCount > 1
        }
        XCTAssertEqual(state.iterationCount, 2)
    }

    func testPollUntilReturnsOnError() async throws {
        let client = IncrementingClient(initialCount: 1, targetCount: 1)
        await client.setErrorState(WorkflowError(message: "Edit failed", retryable: true))

        let poller = PollingManager(client: client, interval: .milliseconds(50))
        let state = try await poller.pollUntil(projectId: "test-123") { _ in false }
        XCTAssertNotNil(state.error)
        XCTAssertEqual(state.error?.message, "Edit failed")
    }

    func testPollUntilRetriesTransientErrors() async throws {
        // Fail once, then return matching condition
        let client = FlakyConditionClient(failCount: 1, iterationCount: 3)
        let poller = PollingManager(client: client, interval: .milliseconds(50), maxRetries: 3)

        let state = try await poller.pollUntil(projectId: "test-123") { state in
            state.iterationCount >= 3
        }
        XCTAssertEqual(state.iterationCount, 3)
        let count = await client.callCount
        XCTAssertEqual(count, 2) // 1 failure + 1 success
    }

    func testPollUntilThrowsAfterMaxRetries() async throws {
        let client = FlakyConditionClient(failCount: 10, iterationCount: 3)
        let poller = PollingManager(client: client, interval: .milliseconds(50), maxRetries: 2)

        do {
            _ = try await poller.pollUntil(projectId: "test-123") { _ in true }
            XCTFail("Should have thrown after max retries")
        } catch let error as APIError {
            XCTAssertTrue(error.isRetryable)
        }
        let count = await client.callCount
        XCTAssertEqual(count, 3) // maxRetries + 1
    }

    // MARK: - Cancellation

    func testPollThrowsCancellationWhenCancelled() async throws {
        let client = MockWorkflowClient(delay: .milliseconds(100))
        let projectId = try await client.createProject(deviceFingerprint: "test", hasLidar: false)
        let poller = PollingManager(client: client, interval: .seconds(10))

        let task = Task {
            try await poller.pollUntilStepChanges(projectId: projectId, currentStep: "photos")
        }

        // Cancel after a brief delay
        try await Task.sleep(for: .milliseconds(50))
        task.cancel()

        do {
            _ = try await task.value
            XCTFail("Should have thrown CancellationError")
        } catch is CancellationError {
            // Expected
        } catch {
            // Task.sleep throws CancellationError which is fine
        }
    }
}

// MARK: - Flaky Client for Testing Retries

/// A minimal client that fails a configured number of times before succeeding.
/// Actor provides compile-time data race protection for mutable callCount.
private actor FlakyClient: WorkflowClientProtocol {
    var callCount = 0
    let failCount: Int
    let stepAfterFail: String
    var errorToThrow: APIError?
    var errorState: WorkflowError?

    init(failCount: Int, stepAfterFail: String) {
        self.failCount = failCount
        self.stepAfterFail = stepAfterFail
    }

    func setErrorToThrow(_ error: APIError) { errorToThrow = error }
    func setErrorState(_ error: WorkflowError) { errorState = error }

    func getState(projectId: String) async throws -> WorkflowState {
        callCount += 1
        if callCount <= failCount {
            throw errorToThrow ?? APIError.networkError(URLError(.notConnectedToInternet))
        }
        var state = WorkflowState(step: stepAfterFail)
        state.error = errorState
        return state
    }

    // Unused stubs — only getState matters for polling tests
    func createProject(deviceFingerprint: String, hasLidar: Bool) async throws -> String { "" }
    func deleteProject(projectId: String) async throws {}
    func deletePhoto(projectId: String, photoId: String) async throws {}
    func updatePhotoNote(projectId: String, photoId: String, note: String?) async throws {}
    func confirmPhotos(projectId: String) async throws {}
    func uploadPhoto(projectId: String, imageData: Data, photoType: String) async throws -> PhotoUploadResponse {
        PhotoUploadResponse(photoId: "", validation: ValidatePhotoOutput(passed: true, failures: [], messages: []))
    }
    func uploadScan(projectId: String, scanData: [String: Any]) async throws {}
    func skipScan(projectId: String) async throws {}
    func startIntake(projectId: String, mode: String) async throws -> IntakeChatOutput {
        IntakeChatOutput(agentMessage: "")
    }
    func sendIntakeMessage(projectId: String, message: String, conversationHistory: [ChatMessage], mode: String?) async throws -> IntakeChatOutput {
        IntakeChatOutput(agentMessage: "")
    }
    func confirmIntake(projectId: String, brief: DesignBrief) async throws {}
    func skipIntake(projectId: String) async throws {}
    func selectOption(projectId: String, index: Int) async throws {}
    func submitAnnotationEdit(projectId: String, annotations: [AnnotationRegion]) async throws {}
    func submitTextFeedback(projectId: String, feedback: String) async throws {}
    func approveDesign(projectId: String) async throws {}
    func startOver(projectId: String) async throws {}
    func retryFailedStep(projectId: String) async throws {}
}

/// Returns a state where iterationCount increments each call until it reaches targetCount.
private actor IncrementingClient: WorkflowClientProtocol {
    var currentCount: Int
    let targetCount: Int
    var errorState: WorkflowError?

    init(initialCount: Int, targetCount: Int) {
        self.currentCount = initialCount
        self.targetCount = targetCount
    }

    func setErrorState(_ error: WorkflowError) { errorState = error }

    func getState(projectId: String) async throws -> WorkflowState {
        if currentCount < targetCount {
            currentCount += 1
        }
        var state = WorkflowState(step: "iteration", iterationCount: currentCount)
        state.error = errorState
        return state
    }

    func createProject(deviceFingerprint: String, hasLidar: Bool) async throws -> String { "" }
    func deleteProject(projectId: String) async throws {}
    func deletePhoto(projectId: String, photoId: String) async throws {}
    func updatePhotoNote(projectId: String, photoId: String, note: String?) async throws {}
    func confirmPhotos(projectId: String) async throws {}
    func uploadPhoto(projectId: String, imageData: Data, photoType: String) async throws -> PhotoUploadResponse {
        PhotoUploadResponse(photoId: "", validation: ValidatePhotoOutput(passed: true, failures: [], messages: []))
    }
    func uploadScan(projectId: String, scanData: [String: Any]) async throws {}
    func skipScan(projectId: String) async throws {}
    func startIntake(projectId: String, mode: String) async throws -> IntakeChatOutput { IntakeChatOutput(agentMessage: "") }
    func sendIntakeMessage(projectId: String, message: String, conversationHistory: [ChatMessage], mode: String?) async throws -> IntakeChatOutput { IntakeChatOutput(agentMessage: "") }
    func confirmIntake(projectId: String, brief: DesignBrief) async throws {}
    func skipIntake(projectId: String) async throws {}
    func selectOption(projectId: String, index: Int) async throws {}
    func submitAnnotationEdit(projectId: String, annotations: [AnnotationRegion]) async throws {}
    func submitTextFeedback(projectId: String, feedback: String) async throws {}
    func approveDesign(projectId: String) async throws {}
    func startOver(projectId: String) async throws {}
    func retryFailedStep(projectId: String) async throws {}
}

/// Fails a configured number of times before returning a state matching a condition.
private actor FlakyConditionClient: WorkflowClientProtocol {
    var callCount = 0
    let failCount: Int
    let iterationCount: Int

    init(failCount: Int, iterationCount: Int) {
        self.failCount = failCount
        self.iterationCount = iterationCount
    }

    func getState(projectId: String) async throws -> WorkflowState {
        callCount += 1
        if callCount <= failCount {
            throw APIError.networkError(URLError(.notConnectedToInternet))
        }
        return WorkflowState(step: "iteration", iterationCount: iterationCount)
    }

    func createProject(deviceFingerprint: String, hasLidar: Bool) async throws -> String { "" }
    func deleteProject(projectId: String) async throws {}
    func deletePhoto(projectId: String, photoId: String) async throws {}
    func updatePhotoNote(projectId: String, photoId: String, note: String?) async throws {}
    func confirmPhotos(projectId: String) async throws {}
    func uploadPhoto(projectId: String, imageData: Data, photoType: String) async throws -> PhotoUploadResponse {
        PhotoUploadResponse(photoId: "", validation: ValidatePhotoOutput(passed: true, failures: [], messages: []))
    }
    func uploadScan(projectId: String, scanData: [String: Any]) async throws {}
    func skipScan(projectId: String) async throws {}
    func startIntake(projectId: String, mode: String) async throws -> IntakeChatOutput { IntakeChatOutput(agentMessage: "") }
    func sendIntakeMessage(projectId: String, message: String, conversationHistory: [ChatMessage], mode: String?) async throws -> IntakeChatOutput { IntakeChatOutput(agentMessage: "") }
    func confirmIntake(projectId: String, brief: DesignBrief) async throws {}
    func skipIntake(projectId: String) async throws {}
    func selectOption(projectId: String, index: Int) async throws {}
    func submitAnnotationEdit(projectId: String, annotations: [AnnotationRegion]) async throws {}
    func submitTextFeedback(projectId: String, feedback: String) async throws {}
    func approveDesign(projectId: String) async throws {}
    func startOver(projectId: String) async throws {}
    func retryFailedStep(projectId: String) async throws {}
}
