// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoTileMode",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoTileMode", targets: ["RemoTileMode"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
        .package(path: "../RemoNetworking"),
        .package(path: "../RemoLiDAR"),
    ],
    targets: [
        .target(
            name: "RemoTileMode",
            dependencies: [
                .product(name: "RemoModels", package: "RemoModels"),
                .product(name: "RemoNetworking", package: "RemoNetworking"),
                .product(name: "RemoLiDAR", package: "RemoLiDAR"),
            ]
        ),
        .testTarget(
            name: "RemoTileModeTests",
            dependencies: ["RemoTileMode"]
        ),
    ]
)
