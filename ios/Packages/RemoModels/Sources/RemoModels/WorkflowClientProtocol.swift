import Foundation

/// Abstraction over the backend API.
/// MockWorkflowClient (P1) and RealWorkflowClient (P2) both conform.
/// All views and view models depend on this protocol, never a concrete client.
public protocol WorkflowClientProtocol: Sendable {
    func createProject(deviceFingerprint: String, hasLidar: Bool) async throws -> String
    func getState(projectId: String) async throws -> WorkflowState
    func deleteProject(projectId: String) async throws

    // Photos
    func uploadPhoto(projectId: String, imageData: Data, photoType: String) async throws -> PhotoUploadResponse
    func deletePhoto(projectId: String, photoId: String) async throws
    func updatePhotoNote(projectId: String, photoId: String, note: String?) async throws
    func confirmPhotos(projectId: String) async throws

    // Scan
    func uploadScan(projectId: String, scanData: [String: Any]) async throws
    func skipScan(projectId: String) async throws

    // Intake
    func startIntake(projectId: String, mode: String) async throws -> IntakeChatOutput
    func sendIntakeMessage(projectId: String, message: String, conversationHistory: [ChatMessage], mode: String?) async throws -> IntakeChatOutput
    func confirmIntake(projectId: String, brief: DesignBrief) async throws
    func skipIntake(projectId: String) async throws

    // Selection
    func selectOption(projectId: String, index: Int) async throws

    // Iteration
    func submitAnnotationEdit(projectId: String, annotations: [AnnotationRegion]) async throws
    func submitTextFeedback(projectId: String, feedback: String) async throws

    // Approval & other
    func approveDesign(projectId: String) async throws
    func startOver(projectId: String) async throws
    func retryFailedStep(projectId: String) async throws
}
