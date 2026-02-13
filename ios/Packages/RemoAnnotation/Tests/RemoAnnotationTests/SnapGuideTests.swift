import XCTest
@testable import RemoAnnotation
import RemoModels

final class SnapGuideTests: XCTestCase {

    // MARK: - Center snap

    func testSnapsToCenterX() {
        let guides = computeSnapGuides(x: 0.501, y: 0.3, excludingRegionId: 1, regions: [])
        XCTAssertTrue(guides.snapX)
        XCTAssertEqual(guides.snappedX, 0.5)
        XCTAssertEqual(guides.verticalLines, [0.5])
        XCTAssertFalse(guides.snapY)
    }

    func testSnapsToCenterY() {
        let guides = computeSnapGuides(x: 0.2, y: 0.49, excludingRegionId: 1, regions: [])
        XCTAssertFalse(guides.snapX)
        XCTAssertTrue(guides.snapY)
        XCTAssertEqual(guides.snappedY, 0.5)
        XCTAssertEqual(guides.horizontalLines, [0.5])
    }

    func testSnapsToBothCenterAxes() {
        let guides = computeSnapGuides(x: 0.505, y: 0.495, excludingRegionId: 1, regions: [])
        XCTAssertTrue(guides.snapX)
        XCTAssertTrue(guides.snapY)
        XCTAssertEqual(guides.snappedX, 0.5)
        XCTAssertEqual(guides.snappedY, 0.5)
    }

    func testNoSnapWhenFarFromCenter() {
        let guides = computeSnapGuides(x: 0.3, y: 0.7, excludingRegionId: 1, regions: [])
        XCTAssertFalse(guides.snapX)
        XCTAssertFalse(guides.snapY)
        XCTAssertTrue(guides.verticalLines.isEmpty)
        XCTAssertTrue(guides.horizontalLines.isEmpty)
    }

    // MARK: - Region alignment snap

    func testSnapsToOtherRegionX() {
        let other = AnnotationRegion(regionId: 2, centerX: 0.7, centerY: 0.2, radius: 0.08, instruction: "test region")
        let guides = computeSnapGuides(x: 0.705, y: 0.8, excludingRegionId: 1, regions: [other])
        XCTAssertTrue(guides.snapX)
        XCTAssertEqual(guides.snappedX, 0.7)
    }

    func testSnapsToOtherRegionY() {
        let other = AnnotationRegion(regionId: 2, centerX: 0.3, centerY: 0.6, radius: 0.08, instruction: "test region")
        let guides = computeSnapGuides(x: 0.8, y: 0.605, excludingRegionId: 1, regions: [other])
        XCTAssertTrue(guides.snapY)
        XCTAssertEqual(guides.snappedY, 0.6)
    }

    func testExcludesSelfFromSnapping() {
        let self1 = AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.5, radius: 0.08, instruction: "self region")
        // Even though region 1 is at center, excluding it means no region snap
        // But center snap still applies
        let guides = computeSnapGuides(x: 0.505, y: 0.3, excludingRegionId: 1, regions: [self1])
        // Should snap to canvas center, not to self
        XCTAssertTrue(guides.snapX)
        XCTAssertEqual(guides.snappedX, 0.5)
    }

    func testCenterTakesPriorityOverRegion() {
        // Both canvas center and a region are near x=0.5
        let other = AnnotationRegion(regionId: 2, centerX: 0.51, centerY: 0.2, radius: 0.08, instruction: "test region")
        let guides = computeSnapGuides(x: 0.505, y: 0.8, excludingRegionId: 1, regions: [other])
        // Canvas center snap fires first
        XCTAssertTrue(guides.snapX)
        XCTAssertEqual(guides.snappedX, 0.5)
    }

    // MARK: - Custom threshold

    func testCustomThreshold() {
        // With a larger threshold, things that didn't snap before now do
        let guides = computeSnapGuides(x: 0.45, y: 0.3, excludingRegionId: 1, regions: [], threshold: 0.1)
        XCTAssertTrue(guides.snapX)
        XCTAssertEqual(guides.snappedX, 0.5)
    }

    func testTightThreshold() {
        // With a very tight threshold, only exact matches snap
        let guides = computeSnapGuides(x: 0.501, y: 0.3, excludingRegionId: 1, regions: [], threshold: 0.001)
        XCTAssertFalse(guides.snapX)
    }
}

// MARK: - Overlap Detection Tests

final class OverlapDetectionTests: XCTestCase {

    func testNoOverlapWithEmptyRegions() {
        let result = checkRegionOverlap(x: 0.5, y: 0.5, radius: 0.08, existingRegions: [])
        XCTAssertFalse(result)
    }

    func testNoOverlapWhenFarApart() {
        let existing = AnnotationRegion(regionId: 1, centerX: 0.2, centerY: 0.2, radius: 0.08, instruction: "test region")
        let result = checkRegionOverlap(x: 0.8, y: 0.8, radius: 0.08, existingRegions: [existing])
        XCTAssertFalse(result)
    }

    func testOverlapWhenIntersecting() {
        let existing = AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.5, radius: 0.1, instruction: "test region")
        // Place new region at (0.55, 0.5) with radius 0.08 — distance=0.05, sum of radii=0.18 → overlaps
        let result = checkRegionOverlap(x: 0.55, y: 0.5, radius: 0.08, existingRegions: [existing])
        XCTAssertTrue(result)
    }

    func testOverlapWhenContained() {
        let existing = AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.5, radius: 0.2, instruction: "large region")
        // New region at the same center — fully contained
        let result = checkRegionOverlap(x: 0.5, y: 0.5, radius: 0.05, existingRegions: [existing])
        XCTAssertTrue(result)
    }

    func testNoOverlapWhenJustTouching() {
        // Two circles exactly touching: distance = sum of radii
        // The check uses strict less-than, so touching should NOT count as overlap
        let existing = AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.5, radius: 0.1, instruction: "test region")
        // Place at (0.68, 0.5) with radius 0.08 — distance=0.18, sum of radii=0.18 → NOT overlapping
        let result = checkRegionOverlap(x: 0.68, y: 0.5, radius: 0.08, existingRegions: [existing])
        XCTAssertFalse(result)
    }

    func testOverlapChecksAllRegions() {
        let regions = [
            AnnotationRegion(regionId: 1, centerX: 0.2, centerY: 0.2, radius: 0.08, instruction: "region one1"),
            AnnotationRegion(regionId: 2, centerX: 0.8, centerY: 0.8, radius: 0.08, instruction: "region two2"),
        ]
        // Overlaps with region 2 only
        let result = checkRegionOverlap(x: 0.82, y: 0.8, radius: 0.08, existingRegions: regions)
        XCTAssertTrue(result)
    }

    func testNoOverlapWithMultipleDistantRegions() {
        let regions = [
            AnnotationRegion(regionId: 1, centerX: 0.1, centerY: 0.1, radius: 0.05, instruction: "corner one"),
            AnnotationRegion(regionId: 2, centerX: 0.9, centerY: 0.1, radius: 0.05, instruction: "corner two"),
            AnnotationRegion(regionId: 3, centerX: 0.1, centerY: 0.9, radius: 0.05, instruction: "corner tre"),
        ]
        // Center of canvas — far from all corners
        let result = checkRegionOverlap(x: 0.5, y: 0.5, radius: 0.08, existingRegions: regions)
        XCTAssertFalse(result)
    }
}
