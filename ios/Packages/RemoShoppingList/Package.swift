// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoShoppingList",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoShoppingList", targets: ["RemoShoppingList"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
        .package(path: "../RemoNetworking"),
    ],
    targets: [
        .target(name: "RemoShoppingList", dependencies: ["RemoModels", "RemoNetworking"]),
    ]
)
