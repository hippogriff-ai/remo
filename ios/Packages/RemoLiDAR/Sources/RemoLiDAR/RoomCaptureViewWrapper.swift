#if canImport(RoomPlan)
import SwiftUI
import RoomPlan

/// Holds a reference to the active capture session so SwiftUI can call stop().
class CaptureSessionRef {
    var session: RoomCaptureSession?

    func stop() {
        session?.stop()
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
        view.captureSession.run(configuration: RoomCaptureSession.Configuration())
        return view
    }

    func updateUIView(_ uiView: RoomCaptureView, context: Context) {}

    static func dismantleUIView(_ uiView: RoomCaptureView, coordinator: RoomCaptureCoordinator) {
        uiView.captureSession.stop()
    }

    func makeCoordinator() -> RoomCaptureCoordinator {
        RoomCaptureCoordinator(onComplete: onComplete)
    }
}
#endif
