import XCTest
@testable import RemoTileMode

final class TileContractsTests: XCTestCase {
    func testMaterialSpecRoundtrip() throws {
        let json = """
        {
          "material_id": "m1",
          "module_width_m": 0.3,
          "module_height_m": 0.6,
          "grout_width_mm": 3.0,
          "unit_of_sale": "box",
          "modules_per_unit": 10,
          "price_per_box_cents": 4200
        }
        """.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(MaterialSpec.self, from: json)
        XCTAssertEqual(decoded.materialId, "m1")
        XCTAssertEqual(decoded.moduleWidthM, 0.3, accuracy: 1e-9)
        XCTAssertEqual(decoded.pricePerBoxCents, 4200)
    }

    func testOveragePolicyRawValuesMatchBackend() {
        XCTAssertEqual(OveragePolicy.flat10.rawValue, "flat_10")
        XCTAssertEqual(OveragePolicy.riskAdjusted.rawValue, "risk_adjusted")
        XCTAssertEqual(OveragePolicy.contractorTier.rawValue, "contractor_tier")
    }
}
