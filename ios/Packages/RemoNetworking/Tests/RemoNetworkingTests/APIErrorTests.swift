import XCTest
@testable import RemoNetworking
import RemoModels

final class APIErrorTests: XCTestCase {

    // MARK: - isRetryable

    func testNetworkErrorIsRetryable() {
        let error = APIError.networkError(URLError(.notConnectedToInternet))
        XCTAssertTrue(error.isRetryable)
    }

    func testCancelledNetworkErrorIsNotRetryable() {
        let error = APIError.networkError(URLError(.cancelled))
        XCTAssertFalse(error.isRetryable)
    }

    func testHTTP404WithNonRetryableIsNotRetryable() {
        let response = ErrorResponse(error: "not_found", message: "Not found", retryable: false)
        let error = APIError.httpError(statusCode: 404, response: response)
        XCTAssertFalse(error.isRetryable)
    }

    func testHTTP500WithNonRetryableResponseIsStillRetryable() {
        // Server errors (>= 500) override the response.retryable flag
        let response = ErrorResponse(error: "server_error", message: "Internal error", retryable: false)
        let error = APIError.httpError(statusCode: 500, response: response)
        XCTAssertTrue(error.isRetryable)
    }

    func testHTTP409WithRetryableResponseIsRetryable() {
        let response = ErrorResponse(error: "wrong_step", message: "Wrong step", retryable: true)
        let error = APIError.httpError(statusCode: 409, response: response)
        XCTAssertTrue(error.isRetryable)
    }

    func testDecodingErrorIsNotRetryable() {
        let context = DecodingError.Context(codingPath: [], debugDescription: "test")
        let error = APIError.decodingError(.dataCorrupted(context))
        XCTAssertFalse(error.isRetryable)
    }

    func testUnknownErrorIsRetryable() {
        let error = APIError.unknown(NSError(domain: "test", code: -1))
        XCTAssertTrue(error.isRetryable)
    }

    func testUnknownCancellationErrorIsNotRetryable() {
        let error = APIError.unknown(CancellationError())
        XCTAssertFalse(error.isRetryable)
    }

    // MARK: - errorDescription

    func testHTTPErrorUsesResponseMessage() {
        let response = ErrorResponse(error: "wrong_step", message: "Cannot upload in step 'intake'", retryable: false)
        let error = APIError.httpError(statusCode: 409, response: response)
        XCTAssertEqual(error.errorDescription, "Cannot upload in step 'intake'")
    }
}
