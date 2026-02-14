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

    func testHTTPErrorIncludesRequestId() {
        var response = ErrorResponse(error: "server_error", message: "Something went wrong", retryable: true)
        response.requestId = "req-abc-123"
        let error = APIError.httpError(statusCode: 500, response: response)
        XCTAssertEqual(error.errorDescription, "Something went wrong\n(Reference: req-abc-123)")
    }

    func testHTTPErrorOmitsRequestIdWhenNil() {
        let response = ErrorResponse(error: "not_found", message: "Project not found", retryable: false)
        let error = APIError.httpError(statusCode: 404, response: response)
        // No requestId → no reference line appended
        XCTAssertEqual(error.errorDescription, "Project not found")
        XCTAssertFalse(error.errorDescription?.contains("Reference") ?? true)
    }

    // MARK: - isCancellation

    func testNetworkCancelledIsCancellation() {
        let error = APIError.networkError(URLError(.cancelled))
        XCTAssertTrue(error.isCancellation)
    }

    func testUnknownCancellationIsCancellation() {
        let error = APIError.unknown(CancellationError())
        XCTAssertTrue(error.isCancellation)
    }

    func testHTTPErrorIsNotCancellation() {
        let response = ErrorResponse(error: "server_error", message: "Error", retryable: true)
        let error = APIError.httpError(statusCode: 500, response: response)
        XCTAssertFalse(error.isCancellation)
    }

    func testDecodingErrorIsNotCancellation() {
        let context = DecodingError.Context(codingPath: [], debugDescription: "test")
        let error = APIError.decodingError(.dataCorrupted(context))
        XCTAssertFalse(error.isCancellation)
    }

    func testNonCancelledURLErrorIsNotCancellation() {
        let error = APIError.networkError(URLError(.timedOut))
        XCTAssertFalse(error.isCancellation)
    }

    func testUnknownNonCancellationErrorIsNotCancellation() {
        let error = APIError.unknown(NSError(domain: "test", code: -1))
        XCTAssertFalse(error.isCancellation)
    }
}
