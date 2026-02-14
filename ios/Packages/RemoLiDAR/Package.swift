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
        .package(path: "../RemoNetworking"),
    ],
    targets: [
        .target(
            name: "RemoLiDAR",
            dependencies: ["RemoModels", "RemoNetworking"],
            linkerSettings: [
                .linkedFramework("ARKit", .when(platforms: [.iOS])),
                .linkedFramework("RoomPlan", .when(platforms: [.iOS])),
            ]
        ),
        .testTarget(
            name: "RemoLiDARTests",
            dependencies: ["RemoLiDAR"]
        ),
    ]
)
