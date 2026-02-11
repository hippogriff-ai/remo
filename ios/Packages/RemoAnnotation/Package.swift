// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoAnnotation",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoAnnotation", targets: ["RemoAnnotation"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
        .package(path: "../RemoNetworking"),
    ],
    targets: [
        .target(name: "RemoAnnotation", dependencies: ["RemoModels", "RemoNetworking"]),
        .testTarget(name: "RemoAnnotationTests", dependencies: ["RemoAnnotation", "RemoModels"]),
    ]
)
