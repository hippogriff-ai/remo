// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoChatUI",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RemoChatUI", targets: ["RemoChatUI"]),
    ],
    dependencies: [
        .package(path: "../RemoModels"),
    ],
    targets: [
        .target(name: "RemoChatUI", dependencies: ["RemoModels"]),
    ]
)
