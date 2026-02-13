import Foundation
import RemoModels

/// Errors from the Remo API.
public enum APIError: LocalizedError {
    case networkError(URLError)
    case httpError(statusCode: Int, response: ErrorResponse)
    case decodingError(DecodingError)
    case unknown(Error)

    public var errorDescription: String? {
        switch self {
        case .networkError(let error):
            return "Network error: \(error.localizedDescription)"
        case .httpError(_, let response):
            if let requestId = response.requestId {
                return "\(response.message)\n(Reference: \(requestId))"
            }
            return response.message
        case .decodingError(let error):
            return "Data error: \(error.localizedDescription)"
        case .unknown(let error):
            return error.localizedDescription
        }
    }

    public var isCancellation: Bool {
        switch self {
        case .networkError(let urlError):
            return urlError.code == .cancelled
        case .unknown(let error):
            return error is CancellationError
        default:
            return false
        }
    }

    public var isRetryable: Bool {
        switch self {
        case .networkError(let urlError):
            // Not retryable if the request was cancelled
            return urlError.code != .cancelled
        case .httpError(let code, let response):
            return response.retryable || code >= 500
        case .decodingError:
            return false
        case .unknown(let error):
            // Don't retry cancellations
            return !(error is CancellationError)
        }
    }
}
