// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoLiDAR",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoLiDAR", targets: ["RemoLiDAR"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
    ],
    targets: [
        .target(name: "RemoLiDAR", dependencies: ["RemoModels"]),
    ]
)
