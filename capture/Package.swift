// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "rec-capture",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "rec-capture", path: "Sources/rec-capture", swiftSettings: [.swiftLanguageMode(.v5)])
    ]
)
