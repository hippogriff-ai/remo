#if canImport(RoomPlan)
import SwiftUI
import RoomPlan

/// SwiftUI wrapper for Apple's `RoomCaptureView`.
///
/// Presents the live room scanning UI. Starts the capture session immediately.
/// When the user taps Done, the session ends and `onComplete` is called
/// with the resulting `CapturedRoom` or error.
///
/// Note: We only set `captureSession.delegate` (not `view.delegate`) because
/// `RoomCaptureViewDelegate` requires `NSCoding` conformance. The session delegate
/// is sufficient — we get the result from `CapturedRoomData.finalResults`.
struct RoomCaptureViewWrapper: UIViewRepresentable {
    let onComplete: (Result<CapturedRoom, Error>) -> Void

    func makeUIView(context: Context) -> RoomCaptureView {
        let view = RoomCaptureView(frame: .zero)
        view.captureSession.delegate = context.coordinator
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
