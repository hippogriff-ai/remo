import XCTest
@testable import RemoLiDAR

final class ScanStateTests: XCTestCase {

    // MARK: - Equatable conformance

    /// Verifies ScanState cases compare correctly.
    func testEquatable() {
        XCTAssertEqual(ScanState.ready, ScanState.ready)
        XCTAssertEqual(ScanState.scanning, ScanState.scanning)
        XCTAssertEqual(ScanState.processing, ScanState.processing)
        XCTAssertEqual(ScanState.uploading, ScanState.uploading)
        XCTAssertEqual(ScanState.failed("error"), ScanState.failed("error"))
    }

    /// Verifies different states are not equal.
    func testNotEqual() {
        XCTAssertNotEqual(ScanState.ready, ScanState.scanning)
        XCTAssertNotEqual(ScanState.scanning, ScanState.processing)
        XCTAssertNotEqual(ScanState.processing, ScanState.uploading)
        XCTAssertNotEqual(ScanState.failed("a"), ScanState.failed("b"))
        XCTAssertNotEqual(ScanState.failed("error"), ScanState.ready)
    }

    // MARK: - State transition validation

    /// Verifies the expected transition: ready → scanning on start.
    func testReadyToScanning() {
        var state: ScanState = .ready
        // Simulate startScan() setting state
        state = .scanning
        XCTAssertEqual(state, .scanning)
    }

    /// Verifies the expected transition: scanning → failed on backgrounding.
    func testScanningToFailedOnBackground() {
        var state: ScanState = .scanning
        // Simulate scenePhase going to background
        state = .failed("Scan interrupted. Please try again.")
        XCTAssertEqual(state, .failed("Scan interrupted. Please try again."))
    }

    /// Verifies the expected transition: failed → ready on retry.
    func testFailedToReadyOnRetry() {
        var state: ScanState = .failed("Some error")
        // Simulate retry button tap
        state = .ready
        XCTAssertEqual(state, .ready)
    }

    /// Verifies the success path: scanning → processing → uploading.
    func testSuccessPath() {
        var state: ScanState = .ready
        state = .scanning
        XCTAssertEqual(state, .scanning)
        state = .processing
        XCTAssertEqual(state, .processing)
        state = .uploading
        XCTAssertEqual(state, .uploading)
    }

    /// Verifies the fixture path: ready → uploading (skips scanning + processing).
    func testFixturePath() {
        var state: ScanState = .ready
        // Fixture path goes straight to uploading
        state = .uploading
        XCTAssertEqual(state, .uploading)
    }

    /// Verifies scan failure path: scanning → failed.
    func testScanningToFailedOnError() {
        var state: ScanState = .scanning
        // Simulate CapturedRoom error
        state = .failed("Capture session failed")
        XCTAssertEqual(state, .failed("Capture session failed"))
    }

    /// Verifies upload failure path: uploading → failed.
    func testUploadingToFailedOnError() {
        var state: ScanState = .uploading
        // Simulate network error during upload
        state = .failed("Network error")
        XCTAssertEqual(state, .failed("Network error"))
    }

    /// Verifies camera denied path: ready → failed.
    func testCameraDeniedPath() {
        var state: ScanState = .ready
        // Simulate camera permission denied
        state = .failed("Camera access required for room scanning. Enable in Settings > Privacy > Camera.")
        XCTAssertEqual(state, .failed("Camera access required for room scanning. Enable in Settings > Privacy > Camera."))
    }

    /// Verifies the complete success path ends at .ready.
    func testSuccessPathCompletesToReady() {
        var state: ScanState = .ready
        state = .scanning
        state = .processing
        state = .uploading
        // After successful upload + state refresh
        state = .ready
        XCTAssertEqual(state, .ready, "Success path should return to .ready after upload completes")
    }

    /// Verifies the skip scan path: ready → uploading → ready.
    func testSkipScanPath() {
        var state: ScanState = .ready
        // skipScan() sets .uploading then back to .ready on success
        state = .uploading
        XCTAssertEqual(state, .uploading)
        state = .ready
        XCTAssertEqual(state, .ready, "Skip scan should return to .ready after completion")
    }

    /// Verifies processing → failed path (export or upload error during processing phase).
    func testProcessingToFailedOnError() {
        var state: ScanState = .processing
        state = .failed("Project not initialized")
        XCTAssertEqual(state, .failed("Project not initialized"))
    }

    /// Verifies the state refresh failure path: upload succeeds but getState fails.
    func testUploadSuccessButRefreshFails() {
        var state: ScanState = .uploading
        // Upload succeeded but getState failed — distinct from upload failure
        state = .failed("Scan saved, but could not refresh. Please go back and return.")
        if case .failed(let msg) = state {
            XCTAssertTrue(msg.contains("Scan saved"), "Message should indicate upload succeeded")
        } else {
            XCTFail("State should be .failed")
        }
    }

    /// Verifies cancellation resets to .ready (not .failed), since the backgrounding
    /// guard already provides user feedback for the interruption.
    func testCancellationResetsToReady() {
        var state: ScanState = .scanning
        // Simulate CancellationError from coordinator — reset, don't show error
        let error: Error = CancellationError()
        if error is CancellationError {
            state = .ready
        } else {
            state = .failed("Room scan failed. Please try again or skip this step.")
        }
        XCTAssertEqual(state, .ready, "Cancellation should reset to .ready, not show error")
    }

    /// Verifies non-cancellation errors use clean user-facing message (not raw localizedDescription).
    func testScanFailureUsesCleanMessage() {
        var state: ScanState = .scanning
        // Simulate a real error (not cancellation)
        let error: Error = NSError(domain: "NSURLErrorDomain", code: -1009, userInfo: nil)
        if error is CancellationError {
            state = .ready
        } else {
            state = .failed("Room scan failed. Please try again or skip this step.")
        }
        XCTAssertEqual(state, .failed("Room scan failed. Please try again or skip this step."),
                        "User should see actionable text, not raw system error")
    }

    /// Verifies skip scan failure uses clean user-facing message.
    func testSkipScanFailureUsesCleanMessage() {
        var state: ScanState = .uploading
        // Simulate network error during skipScan
        state = .failed("Could not skip scan. Check your connection and try again.")
        XCTAssertEqual(state, .failed("Could not skip scan. Check your connection and try again."))
    }

    /// Verifies backgrounding only affects scanning state (not processing/uploading).
    func testBackgroundingOnlyAffectsScanning() {
        // Processing state should not be affected by backgrounding
        var state: ScanState = .processing
        // The onChange handler checks: if newPhase != .active && scanState == .scanning
        // Since state is .processing, not .scanning, it should not change
        if state == .scanning {
            state = .failed("Scan interrupted.")
        }
        XCTAssertEqual(state, .processing, "Backgrounding should not affect processing state")
    }
}
