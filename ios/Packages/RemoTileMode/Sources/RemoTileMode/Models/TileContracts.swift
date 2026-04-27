import Foundation

// MARK: - Geometry

public struct TileVec2: Codable, Hashable, Sendable {
    public let x: Double
    public let y: Double
    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

// MARK: - Holes (openings + user masks)

public enum TileHoleKind: String, Codable, Sendable {
    case opening
    case mask
}

public struct TileHole: Codable, Hashable, Sendable {
    public let xM: Double
    public let yM: Double
    public let widthM: Double
    public let heightM: Double
    public let label: String
    public let kind: TileHoleKind

    enum CodingKeys: String, CodingKey {
        case xM = "x_m"
        case yM = "y_m"
        case widthM = "width_m"
        case heightM = "height_m"
        case label
        case kind
    }

    public init(xM: Double, yM: Double, widthM: Double, heightM: Double, label: String, kind: TileHoleKind) {
        self.xM = xM; self.yM = yM
        self.widthM = widthM; self.heightM = heightM
        self.label = label; self.kind = kind
    }
}

// MARK: - SurfacePatch (mirrors backend contracts.SurfacePatch)

public struct SurfacePatch: Codable, Hashable, Sendable {
    public let patchId: String
    public let kind: String  // "wall" | "floor" | "ceiling"
    public let polygon: [TileVec2]
    public let holes: [TileHole]
    public let axis: TileVec2
    public let origin: TileVec2
    public let normalX: Double
    public let normalY: Double
    public let normalZ: Double

    enum CodingKeys: String, CodingKey {
        case patchId = "patch_id"
        case kind, polygon, holes, axis, origin
        case normalX = "normal_x"
        case normalY = "normal_y"
        case normalZ = "normal_z"
    }

    public init(
        patchId: String,
        kind: String,
        polygon: [TileVec2],
        holes: [TileHole] = [],
        axis: TileVec2 = TileVec2(x: 1, y: 0),
        origin: TileVec2 = TileVec2(x: 0, y: 0),
        normalX: Double = 0,
        normalY: Double = 1,
        normalZ: Double = 0
    ) {
        self.patchId = patchId; self.kind = kind
        self.polygon = polygon; self.holes = holes
        self.axis = axis; self.origin = origin
        self.normalX = normalX; self.normalY = normalY; self.normalZ = normalZ
    }
}

// MARK: - MaterialSpec

public struct MaterialSpec: Codable, Hashable, Sendable {
    public let materialId: String
    public let moduleWidthM: Double
    public let moduleHeightM: Double
    public let thicknessMm: Double?
    public let unitOfSale: String
    public let modulesPerUnit: Int
    public let groutWidthMm: Double
    public let pricePerBoxCents: Int?

    enum CodingKeys: String, CodingKey {
        case materialId = "material_id"
        case moduleWidthM = "module_width_m"
        case moduleHeightM = "module_height_m"
        case thicknessMm = "thickness_mm"
        case unitOfSale = "unit_of_sale"
        case modulesPerUnit = "modules_per_unit"
        case groutWidthMm = "grout_width_mm"
        case pricePerBoxCents = "price_per_box_cents"
    }
}

// MARK: - Policies

public enum OveragePolicy: String, Codable, CaseIterable, Identifiable, Sendable {
    case flat10 = "flat_10"
    case riskAdjusted = "risk_adjusted"
    case contractorTier = "contractor_tier"
    public var id: String { rawValue }
    public var displayName: String {
        switch self {
        case .flat10: return "Flat 10%"
        case .riskAdjusted: return "Risk-Adjusted"
        case .contractorTier: return "Contractor Tier"
        }
    }
}

public enum ContractorTier: String, Codable, CaseIterable, Identifiable, Sendable {
    case apprentice, pro, perfectionist
    public var id: String { rawValue }
    public var displayName: String { rawValue.capitalized }
}

public enum StartingPointRule: String, Codable, CaseIterable, Identifiable, Sendable {
    case centeredFocalWall = "centered_focal_wall"
    case largestWallCorner = "largest_wall_corner"
    case minimizeCutCount = "minimize_cut_count"
    public var id: String { rawValue }
    public var displayName: String {
        switch self {
        case .centeredFocalWall: return "Centered"
        case .largestWallCorner: return "Corner Start"
        case .minimizeCutCount: return "Minimize Cuts"
        }
    }
}

// MARK: - Estimate result

public struct TileRect: Codable, Hashable, Sendable {
    public let xM: Double
    public let yM: Double
    public let widthM: Double
    public let heightM: Double
    public let row: Int
    public let col: Int
    public let isCut: Bool

    enum CodingKeys: String, CodingKey {
        case xM = "x_m"
        case yM = "y_m"
        case widthM = "width_m"
        case heightM = "height_m"
        case row, col
        case isCut = "is_cut"
    }
}

public struct PackResult: Codable, Hashable, Sendable {
    public let surfaceId: String
    public let fullModules: Int
    public let cutModules: Int
    public let tilesRequired: Int
    public let rects: [TileRect]
    public let coveredAreaM2: Double
    public let wasteAreaM2: Double
    public let startXM: Double
    public let startYM: Double

    enum CodingKeys: String, CodingKey {
        case surfaceId = "surface_id"
        case fullModules = "full_modules"
        case cutModules = "cut_modules"
        case tilesRequired = "tiles_required"
        case rects
        case coveredAreaM2 = "covered_area_m2"
        case wasteAreaM2 = "waste_area_m2"
        case startXM = "start_x_m"
        case startYM = "start_y_m"
    }
}

public struct TileModeEstimate: Codable, Hashable, Sendable {
    public let wallMaterial: MaterialSpec
    public let floorMaterial: MaterialSpec
    public let wallPacks: [PackResult]
    public let floorPacks: [PackResult]
    public let wallTilesTotal: Int
    public let floorTilesTotal: Int
    public let wallBoxesTotal: Int
    public let floorBoxesTotal: Int
    public let overagePolicy: OveragePolicy
    public let contractorTier: ContractorTier?
    public let startingPointRule: StartingPointRule

    enum CodingKeys: String, CodingKey {
        case wallMaterial = "wall_material"
        case floorMaterial = "floor_material"
        case wallPacks = "wall_packs"
        case floorPacks = "floor_packs"
        case wallTilesTotal = "wall_tiles_total"
        case floorTilesTotal = "floor_tiles_total"
        case wallBoxesTotal = "wall_boxes_total"
        case floorBoxesTotal = "floor_boxes_total"
        case overagePolicy = "overage_policy"
        case contractorTier = "contractor_tier"
        case startingPointRule = "starting_point_rule"
    }
}

// MARK: - API request shapes

public struct CreateTileProjectRequest: Codable {
    public let deviceFingerprint: String
    enum CodingKeys: String, CodingKey { case deviceFingerprint = "device_fingerprint" }
    public init(deviceFingerprint: String) { self.deviceFingerprint = deviceFingerprint }
}

public struct TileSpecInput: Codable, Hashable {
    public var widthCm: Double
    public var heightCm: Double
    public var groutMm: Double
    public var perBox: Int
    public var pricePerBox: Double?

    enum CodingKeys: String, CodingKey {
        case widthCm = "width_cm"
        case heightCm = "height_cm"
        case groutMm = "grout_mm"
        case perBox = "per_box"
        case pricePerBox = "price_per_box"
    }

    public init(widthCm: Double, heightCm: Double, groutMm: Double = 3.0, perBox: Int, pricePerBox: Double? = nil) {
        self.widthCm = widthCm; self.heightCm = heightCm
        self.groutMm = groutMm; self.perBox = perBox
        self.pricePerBox = pricePerBox
    }
}

public struct SetTileMaterialsRequest: Codable {
    public let wall: TileSpecInput
    public let floor: TileSpecInput
    public init(wall: TileSpecInput, floor: TileSpecInput) {
        self.wall = wall; self.floor = floor
    }
}

public struct SetTilePoliciesRequest: Codable {
    public let overagePolicy: OveragePolicy
    public let contractorTier: ContractorTier?
    public let startingPointRule: StartingPointRule

    enum CodingKeys: String, CodingKey {
        case overagePolicy = "overage_policy"
        case contractorTier = "contractor_tier"
        case startingPointRule = "starting_point_rule"
    }

    public init(overagePolicy: OveragePolicy, contractorTier: ContractorTier?, startingPointRule: StartingPointRule) {
        self.overagePolicy = overagePolicy
        self.contractorTier = contractorTier
        self.startingPointRule = startingPointRule
    }
}

// MARK: - Workflow state (query response)

public struct TileWorkflowState: Codable, Sendable {
    public let step: String
    public let surfaces: [SurfacePatch]
    public let wallMaterial: MaterialSpec?
    public let floorMaterial: MaterialSpec?
    public let overagePolicy: OveragePolicy
    public let contractorTier: ContractorTier?
    public let startingPointRule: StartingPointRule
    public let estimate: TileModeEstimate?
    public let renderImageUrl: String?
    public let cutSheetPdfUrl: String?
    public let renderAttemptCount: Int
    public let exportAttemptCount: Int
    public let estimateConfirmed: Bool

    enum CodingKeys: String, CodingKey {
        case step, surfaces, estimate
        case wallMaterial = "wall_material"
        case floorMaterial = "floor_material"
        case overagePolicy = "overage_policy"
        case contractorTier = "contractor_tier"
        case startingPointRule = "starting_point_rule"
        case renderImageUrl = "render_image_url"
        case cutSheetPdfUrl = "cut_sheet_pdf_url"
        case renderAttemptCount = "render_attempt_count"
        case exportAttemptCount = "export_attempt_count"
        case estimateConfirmed = "estimate_confirmed"
    }
}

public enum TileProjectStep: String, CaseIterable {
    case scan = "replace_material_scan"
    case specs = "replace_material_specs"
    case estimate = "replace_material_estimate"
    case render = "replace_material_render"
    case export = "replace_material_export"
    case completed, abandoned, cancelled
}

// MARK: - Tileable objects (e.g. bathtub sides)

/// Object detected by LiDAR that has exposed vertical faces the user might
/// want to tile (typically a bathtub alcove or surround).
public struct TileableObject: Identifiable, Hashable, Sendable {
    public let id: String
    public let label: String
    public let widthM: Double
    public let depthM: Double
    public let heightM: Double

    public init(id: String, label: String, widthM: Double, depthM: Double, heightM: Double) {
        self.id = id; self.label = label
        self.widthM = widthM; self.depthM = depthM; self.heightM = heightM
    }

    public enum Side: String, CaseIterable, Identifiable {
        case front, back, left, right
        public var id: String { rawValue }
        public var displayName: String { rawValue.capitalized }
    }

    /// Build a SurfacePatch for one vertical side of this object. Width /
    /// height come from the object's outer dimensions. Treated as wall-kind
    /// so the wall tile material applies.
    public func patch(for side: Side) -> SurfacePatch {
        let surfaceWidth: Double = (side == .front || side == .back) ? widthM : depthM
        return SurfacePatch(
            patchId: "\(id)_\(side.rawValue)",
            kind: "wall",
            polygon: [
                TileVec2(x: 0, y: 0),
                TileVec2(x: surfaceWidth, y: 0),
                TileVec2(x: surfaceWidth, y: heightM),
                TileVec2(x: 0, y: heightM),
            ],
            holes: [],
            axis: TileVec2(x: 1, y: 0),
            origin: TileVec2(x: 0, y: 0),
            normalX: side == .left ? -1 : (side == .right ? 1 : 0),
            normalY: 0,
            normalZ: side == .front ? 1 : (side == .back ? -1 : 0)
        )
    }
}
