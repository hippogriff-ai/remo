// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoPhotoUpload",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoPhotoUpload", targets: ["RemoPhotoUpload"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
    ],
    targets: [
        .target(name: "RemoPhotoUpload", dependencies: ["RemoModels"]),
    ]
)
