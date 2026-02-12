// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoModels",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoModels", targets: ["RemoModels"]),
    ],
    targets: [
        .target(name: "RemoModels"),
        .testTarget(name: "RemoModelsTests", dependencies: ["RemoModels"]),
    ]
)
