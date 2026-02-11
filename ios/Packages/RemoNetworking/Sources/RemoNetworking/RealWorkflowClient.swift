import Foundation
import RemoModels

/// Real API client — calls the FastAPI backend.
/// Skeleton for P2 integration. All methods make actual HTTP requests.
public final class RealWorkflowClient: WorkflowClientProtocol, @unchecked Sendable {
    private let baseURL: URL
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()
    }

    // MARK: - Project lifecycle

    public func createProject(deviceFingerprint: String, hasLidar: Bool) async throws -> String {
        let body = CreateProjectRequest(deviceFingerprint: deviceFingerprint, hasLidar: hasLidar)
        let response: CreateProjectResponse = try await post("/api/v1/projects", body: body)
        return response.projectId
    }

    public func getState(projectId: String) async throws -> WorkflowState {
        try await get("/api/v1/projects/\(projectId)")
    }

    public func deleteProject(projectId: String) async throws {
        try await delete("/api/v1/projects/\(projectId)")
    }

    // MARK: - Photos

    public func uploadPhoto(projectId: String, imageData: Data, photoType: String) async throws -> PhotoUploadResponse {
        let url = baseURL.appendingPathComponent("/api/v1/projects/\(projectId)/photos")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"

        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        // photo_type field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"photo_type\"\r\n\r\n".data(using: .utf8)!)
        body.append("\(photoType)\r\n".data(using: .utf8)!)
        // file field
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"photo.jpg\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(imageData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        request.httpBody = body

        let (data, response) = try await session.data(for: request)
        try checkHTTPResponse(response, data: data)
        return try decoder.decode(PhotoUploadResponse.self, from: data)
    }

    // MARK: - Scan

    public func uploadScan(projectId: String, scanData: [String: Any]) async throws {
        // scanData is arbitrary JSON, so we serialize manually
        let jsonData = try JSONSerialization.data(withJSONObject: scanData)
        let url = baseURL.appendingPathComponent("/api/v1/projects/\(projectId)/scan")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = jsonData

        let (data, response) = try await session.data(for: request)
        try checkHTTPResponse(response, data: data)
    }

    public func skipScan(projectId: String) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/scan/skip")
    }

    // MARK: - Intake

    public func startIntake(projectId: String, mode: String) async throws -> IntakeChatOutput {
        try await post("/api/v1/projects/\(projectId)/intake/start", body: IntakeStartRequest(mode: mode))
    }

    public func sendIntakeMessage(projectId: String, message: String) async throws -> IntakeChatOutput {
        try await post("/api/v1/projects/\(projectId)/intake/message", body: IntakeMessageRequest(message: message))
    }

    public func confirmIntake(projectId: String, brief: DesignBrief) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/intake/confirm", body: IntakeConfirmRequest(brief: brief))
    }

    public func skipIntake(projectId: String) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/intake/skip")
    }

    // MARK: - Selection

    public func selectOption(projectId: String, index: Int) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/select", body: SelectOptionRequest(index: index))
    }

    // MARK: - Iteration

    public func submitAnnotationEdit(projectId: String, annotations: [AnnotationRegion]) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/iterate/annotate", body: AnnotationEditRequest(annotations: annotations))
    }

    public func submitTextFeedback(projectId: String, feedback: String) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/iterate/feedback", body: TextFeedbackRequest(feedback: feedback))
    }

    // MARK: - Approval & other

    public func approveDesign(projectId: String) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/approve")
    }

    public func startOver(projectId: String) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/start-over")
    }

    public func retryFailedStep(projectId: String) async throws {
        let _: ActionResponse = try await post("/api/v1/projects/\(projectId)/retry")
    }

    // MARK: - HTTP helpers

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        let (data, response) = try await session.data(from: url)
        try checkHTTPResponse(response, data: data)
        return try decoder.decode(T.self, from: data)
    }

    private func post<T: Decodable>(_ path: String) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.data(for: request)
        try checkHTTPResponse(response, data: data)
        return try decoder.decode(T.self, from: data)
    }

    private func post<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: request)
        try checkHTTPResponse(response, data: data)
        return try decoder.decode(T.self, from: data)
    }

    private func delete(_ path: String) async throws {
        let url = baseURL.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        let (data, response) = try await session.data(for: request)
        // 204 No Content is success for DELETE
        if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 204 {
            return
        }
        try checkHTTPResponse(response, data: data)
    }

    private func checkHTTPResponse(_ response: URLResponse, data: Data) throws {
        guard let httpResponse = response as? HTTPURLResponse else { return }
        guard (200...299).contains(httpResponse.statusCode) else {
            if let errorResponse = try? decoder.decode(ErrorResponse.self, from: data) {
                throw APIError.httpError(statusCode: httpResponse.statusCode, response: errorResponse)
            }
            throw APIError.httpError(
                statusCode: httpResponse.statusCode,
                response: ErrorResponse(
                    error: "http_\(httpResponse.statusCode)",
                    message: "Request failed with status \(httpResponse.statusCode)",
                    retryable: httpResponse.statusCode >= 500
                )
            )
        }
    }
}
