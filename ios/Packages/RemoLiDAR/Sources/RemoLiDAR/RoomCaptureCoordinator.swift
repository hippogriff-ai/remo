#if canImport(RoomPlan)
import Foundation
import os
import RoomPlan

private let captureLogger = Logger(subsystem: "com.remo.lidar", category: "capture")

/// Bridges Apple's `RoomCaptureSessionDelegate` to a Swift closure.
///
/// When the capture session ends, uses `RoomBuilder` to convert `CapturedRoomData`
/// into a `CapturedRoom`, then calls the completion handler on the main thread.
///
/// Note: We use `RoomBuilder` instead of `RoomCaptureViewDelegate` because the view
/// delegate requires `NSCoding` conformance (a UIKit archiving protocol not needed here).
class RoomCaptureCoordinator: NSObject, RoomCaptureSessionDelegate {
    let onComplete: (Result<CapturedRoom, Error>) -> Void
    private let roomBuilder = RoomBuilder(options: [.beautifyObjects])
    private var buildTask: Task<Void, Never>?

    init(onComplete: @escaping (Result<CapturedRoom, Error>) -> Void) {
        self.onComplete = onComplete
    }

    deinit {
        if buildTask != nil {
            captureLogger.warning("coordinator deinit: cancelling in-progress RoomBuilder task")
        }
        buildTask?.cancel()
    }

    func captureSession(_ session: RoomCaptureSession, didEndWith data: CapturedRoomData, error: (any Error)?) {
        if let error {
            captureLogger.error("capture session ended with error: \(error.localizedDescription, privacy: .public)")
            Task { @MainActor [self] in
                onComplete(.failure(error))
            }
            return
        }

        captureLogger.info("capture session ended, building CapturedRoom...")
        buildTask = Task {
            do {
                let room = try await roomBuilder.capturedRoom(from: data)
                guard !Task.isCancelled else {
                    captureLogger.warning("RoomBuilder completed but task was cancelled — discarding result")
                    await MainActor.run { [self] in
                        onComplete(.failure(CancellationError()))
                    }
                    return
                }
                captureLogger.info("RoomBuilder completed: \(room.walls.count) walls, \(room.objects.count) objects")
                await MainActor.run { [self] in
                    onComplete(.success(room))
                }
            } catch {
                guard !Task.isCancelled else {
                    captureLogger.warning("RoomBuilder failed and task was cancelled: \(error.localizedDescription, privacy: .public)")
                    await MainActor.run { [self] in
                        onComplete(.failure(CancellationError()))
                    }
                    return
                }
                captureLogger.error("RoomBuilder failed: \(error.localizedDescription, privacy: .public)")
                await MainActor.run { [self] in
                    onComplete(.failure(error))
                }
            }
        }
    }
}
#endif
