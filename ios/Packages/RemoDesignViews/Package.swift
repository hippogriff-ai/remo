// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoDesignViews",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoDesignViews", targets: ["RemoDesignViews"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
        .package(path: "../RemoNetworking"),
        .package(path: "../RemoShoppingList"),
    ],
    targets: [
        .target(name: "RemoDesignViews", dependencies: ["RemoModels", "RemoNetworking", "RemoShoppingList"]),
    ]
)
