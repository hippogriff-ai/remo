// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoNetworking",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoNetworking", targets: ["RemoNetworking"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
    ],
    targets: [
        .target(name: "RemoNetworking", dependencies: ["RemoModels"]),
        .testTarget(name: "RemoNetworkingTests", dependencies: ["RemoNetworking"]),
    ]
)
