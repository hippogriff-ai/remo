import Foundation
import RemoModels
import RemoNetworking

/// Talks to /api/v1/tile-projects/*. Parallel to WorkflowClientProtocol for
/// the Replace Material flow.
public protocol TileWorkflowClient: Sendable {
    func createProject(deviceFingerprint: String) async throws -> String
    func getState(projectId: String) async throws -> TileWorkflowState
    func uploadScan(projectId: String, scanJSON: Data) async throws -> Int  // returns surface_count
    func setSurfaces(projectId: String, surfaces: [SurfacePatch]) async throws
    func setMaterials(projectId: String, wall: TileSpecInput, floor: TileSpecInput) async throws
    func setPolicies(projectId: String, body: SetTilePoliciesRequest) async throws
    func confirmEstimate(projectId: String) async throws
    func requestRender(projectId: String) async throws
    func requestExport(projectId: String) async throws
    func retryRender(projectId: String) async throws
    func cancelProject(projectId: String) async throws
}

public struct RealTileWorkflowClient: TileWorkflowClient {
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

    public func createProject(deviceFingerprint: String) async throws -> String {
        let url = baseURL.appendingPathComponent("/api/v1/tile-projects")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(CreateTileProjectRequest(deviceFingerprint: deviceFingerprint))
        let (data, resp) = try await session.data(for: req)
        try check(resp, data)
        struct CreateResponse: Decodable { let projectId: String
            enum CodingKeys: String, CodingKey { case projectId = "project_id" }
        }
        return try decoder.decode(CreateResponse.self, from: data).projectId
    }

    public func getState(projectId: String) async throws -> TileWorkflowState {
        let url = baseURL.appendingPathComponent("/api/v1/tile-projects/\(projectId)")
        let (data, resp) = try await session.data(from: url)
        try check(resp, data)
        return try decoder.decode(TileWorkflowState.self, from: data)
    }

    public func uploadScan(projectId: String, scanJSON: Data) async throws -> Int {
        let url = baseURL.appendingPathComponent("/api/v1/tile-projects/\(projectId)/scan")
        let boundary = "Boundary-\(UUID().uuidString)"
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append(
            "Content-Disposition: form-data; name=\"file\"; filename=\"scan.json\"\r\n".data(using: .utf8)!
        )
        body.append("Content-Type: application/json\r\n\r\n".data(using: .utf8)!)
        body.append(scanJSON)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body

        let (data, resp) = try await session.data(for: req)
        try check(resp, data)
        struct ScanResponse: Decodable { let status: String; let surfaceCount: Int
            enum CodingKeys: String, CodingKey { case status; case surfaceCount = "surface_count" }
        }
        return try decoder.decode(ScanResponse.self, from: data).surfaceCount
    }

    public func setMaterials(projectId: String, wall: TileSpecInput, floor: TileSpecInput) async throws {
        try await postJSON(
            path: "/api/v1/tile-projects/\(projectId)/materials",
            body: SetTileMaterialsRequest(wall: wall, floor: floor)
        )
    }

    public func setSurfaces(projectId: String, surfaces: [SurfacePatch]) async throws {
        struct Body: Encodable { let surfaces: [SurfacePatch] }
        try await postJSON(
            path: "/api/v1/tile-projects/\(projectId)/surfaces",
            body: Body(surfaces: surfaces)
        )
    }

    public func setPolicies(projectId: String, body: SetTilePoliciesRequest) async throws {
        try await postJSON(path: "/api/v1/tile-projects/\(projectId)/policies", body: body)
    }

    public func confirmEstimate(projectId: String) async throws {
        try await postEmpty(path: "/api/v1/tile-projects/\(projectId)/confirm-estimate")
    }

    public func requestRender(projectId: String) async throws {
        try await postEmpty(path: "/api/v1/tile-projects/\(projectId)/render")
    }

    public func retryRender(projectId: String) async throws {
        try await postEmpty(path: "/api/v1/tile-projects/\(projectId)/retry-render")
    }

    public func requestExport(projectId: String) async throws {
        try await postEmpty(path: "/api/v1/tile-projects/\(projectId)/export")
    }

    public func cancelProject(projectId: String) async throws {
        let url = baseURL.appendingPathComponent("/api/v1/tile-projects/\(projectId)")
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        let (data, resp) = try await session.data(for: req)
        try check(resp, data)
    }

    // MARK: - helpers

    private func postJSON<T: Encodable>(path: String, body: T) async throws {
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(body)
        let (data, resp) = try await session.data(for: req)
        try check(resp, data)
    }

    private func postEmpty(path: String) async throws {
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (data, resp) = try await session.data(for: req)
        try check(resp, data)
    }

    private func check(_ resp: URLResponse, _ data: Data) throws {
        guard let http = resp as? HTTPURLResponse else {
            throw APIError.unknown(URLError(.badServerResponse))
        }
        guard (200..<300).contains(http.statusCode) else {
            let body: ErrorResponse
            if let decoded = try? decoder.decode(ErrorResponse.self, from: data) {
                body = decoded
            } else {
                let snippet = String(data: data.prefix(512), encoding: .utf8) ?? ""
                body = ErrorResponse(
                    error: "http_\(http.statusCode)",
                    message: snippet.isEmpty ? "Request failed" : snippet,
                    retryable: http.statusCode >= 500
                )
            }
            throw APIError.httpError(statusCode: http.statusCode, response: body)
        }
    }
}
