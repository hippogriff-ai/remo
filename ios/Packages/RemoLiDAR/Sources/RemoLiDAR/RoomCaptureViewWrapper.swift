#if canImport(RoomPlan)
import SwiftUI
import RoomPlan

/// Holds a reference to the active capture session so SwiftUI can call stop().
/// Tracks whether stop() has already been called to prevent double-stop crashes
/// in RoomPlan (dismantleUIView fires after Done button already stopped the session).
class CaptureSessionRef {
    var session: RoomCaptureSession?
    private var hasStopped = false

    func stop() {
        guard !hasStopped else { return }
        hasStopped = true
        session?.stop()
    }

    func reset() {
        hasStopped = false
    }
}

/// SwiftUI wrapper for Apple's `RoomCaptureView`.
///
/// Presents the live room scanning UI. Starts the capture session immediately.
/// The `sessionRef` is populated on creation so the parent can call `stop()`
/// via a Done button. When the session ends, `onComplete` fires with the result.
struct RoomCaptureViewWrapper: UIViewRepresentable {
    let sessionRef: CaptureSessionRef
    let onComplete: (Result<CapturedRoom, Error>) -> Void

    func makeUIView(context: Context) -> RoomCaptureView {
        let view = RoomCaptureView(frame: .zero)
        view.captureSession.delegate = context.coordinator
        sessionRef.session = view.captureSession
        sessionRef.reset()
        view.captureSession.run(configuration: RoomCaptureSession.Configuration())
        return view
    }

    func updateUIView(_ uiView: RoomCaptureView, context: Context) {}

    static func dismantleUIView(_ uiView: RoomCaptureView, coordinator: RoomCaptureCoordinator) {
        // Use the guarded sessionRef instead of calling stop() directly.
        // Done/Cancel/timeout paths may have already stopped the session;
        // CaptureSessionRef.stop() is a no-op if already stopped.
        coordinator.sessionRef?.stop()
    }

    func makeCoordinator() -> RoomCaptureCoordinator {
        let coordinator = RoomCaptureCoordinator(onComplete: onComplete)
        coordinator.sessionRef = sessionRef
        return coordinator
    }
}
#endif
