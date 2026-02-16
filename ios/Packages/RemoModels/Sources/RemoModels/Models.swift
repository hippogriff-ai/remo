import Foundation

// MARK: - Typed Enums for Backend Literals

/// Photo type — mirrors backend `Literal["room", "inspiration"]`.
public enum PhotoType: String, Codable, Hashable, Sendable {
    case room
    case inspiration
}

/// Revision type — mirrors backend annotation vs feedback distinction.
public enum RevisionType: String, Codable, Hashable, Sendable {
    case annotation
    case feedback
}

// MARK: - Shared Types (mirrors backend/app/models/contracts.py)

public struct StyleProfile: Codable, Hashable, Sendable {
    public var lighting: String?
    public var colors: [String]
    public var textures: [String]
    public var clutterLevel: String?
    public var mood: String?

    public init(
        lighting: String? = nil,
        colors: [String] = [],
        textures: [String] = [],
        clutterLevel: String? = nil,
        mood: String? = nil
    ) {
        self.lighting = lighting
        self.colors = colors
        self.textures = textures
        self.clutterLevel = clutterLevel
        self.mood = mood
    }

    enum CodingKeys: String, CodingKey {
        case lighting, colors, textures
        case clutterLevel = "clutter_level"
        case mood
    }
}

public struct InspirationNote: Codable, Hashable, Sendable {
    public var photoIndex: Int
    public var note: String
    public var agentClarification: String?

    public init(photoIndex: Int, note: String, agentClarification: String? = nil) {
        self.photoIndex = photoIndex
        self.note = note
        self.agentClarification = agentClarification
    }

    enum CodingKeys: String, CodingKey {
        case photoIndex = "photo_index"
        case note
        case agentClarification = "agent_clarification"
    }
}

public struct DesignBrief: Codable, Hashable, Sendable {
    public var roomType: String
    public var occupants: String?
    public var painPoints: [String]
    public var keepItems: [String]
    public var styleProfile: StyleProfile?
    public var constraints: [String]
    public var inspirationNotes: [InspirationNote]

    public init(
        roomType: String,
        occupants: String? = nil,
        painPoints: [String] = [],
        keepItems: [String] = [],
        styleProfile: StyleProfile? = nil,
        constraints: [String] = [],
        inspirationNotes: [InspirationNote] = []
    ) {
        self.roomType = roomType
        self.occupants = occupants
        self.painPoints = painPoints
        self.keepItems = keepItems
        self.styleProfile = styleProfile
        self.constraints = constraints
        self.inspirationNotes = inspirationNotes
    }

    enum CodingKeys: String, CodingKey {
        case roomType = "room_type"
        case occupants
        case painPoints = "pain_points"
        case keepItems = "keep_items"
        case styleProfile = "style_profile"
        case constraints
        case inspirationNotes = "inspiration_notes"
    }
}

public struct RoomDimensions: Codable, Hashable, Sendable {
    public var widthM: Double
    public var lengthM: Double
    public var heightM: Double
    public var walls: [[String: AnyCodable]]
    public var openings: [[String: AnyCodable]]
    public var furniture: [[String: AnyCodable]]
    public var surfaces: [[String: AnyCodable]]
    public var floorAreaSqm: Double?

    public init(
        widthM: Double,
        lengthM: Double,
        heightM: Double,
        walls: [[String: AnyCodable]] = [],
        openings: [[String: AnyCodable]] = [],
        furniture: [[String: AnyCodable]] = [],
        surfaces: [[String: AnyCodable]] = [],
        floorAreaSqm: Double? = nil
    ) {
        self.widthM = widthM
        self.lengthM = lengthM
        self.heightM = heightM
        self.walls = walls
        self.openings = openings
        self.furniture = furniture
        self.surfaces = surfaces
        self.floorAreaSqm = floorAreaSqm
    }

    enum CodingKeys: String, CodingKey {
        case widthM = "width_m"
        case lengthM = "length_m"
        case heightM = "height_m"
        case walls, openings, furniture, surfaces
        case floorAreaSqm = "floor_area_sqm"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        widthM = try container.decode(Double.self, forKey: .widthM)
        lengthM = try container.decode(Double.self, forKey: .lengthM)
        heightM = try container.decode(Double.self, forKey: .heightM)
        walls = try container.decodeIfPresent([[String: AnyCodable]].self, forKey: .walls) ?? []
        openings = try container.decodeIfPresent([[String: AnyCodable]].self, forKey: .openings) ?? []
        furniture = try container.decodeIfPresent([[String: AnyCodable]].self, forKey: .furniture) ?? []
        surfaces = try container.decodeIfPresent([[String: AnyCodable]].self, forKey: .surfaces) ?? []
        floorAreaSqm = try container.decodeIfPresent(Double.self, forKey: .floorAreaSqm)
    }
}

public struct AnnotationRegion: Codable, Hashable, Sendable {
    public var regionId: Int
    public var centerX: Double
    public var centerY: Double
    public var radius: Double
    public var instruction: String
    public var action: String?
    public var avoid: [String]
    public var constraints: [String]

    public init(
        regionId: Int,
        centerX: Double,
        centerY: Double,
        radius: Double,
        instruction: String,
        action: String? = nil,
        avoid: [String] = [],
        constraints: [String] = []
    ) {
        self.regionId = regionId
        self.centerX = centerX
        self.centerY = centerY
        self.radius = radius
        self.instruction = instruction
        self.action = action
        self.avoid = avoid
        self.constraints = constraints
    }

    enum CodingKeys: String, CodingKey {
        case regionId = "region_id"
        case centerX = "center_x"
        case centerY = "center_y"
        case radius
        case instruction
        case action
        case avoid
        case constraints
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        regionId = try container.decode(Int.self, forKey: .regionId)
        centerX = try container.decode(Double.self, forKey: .centerX)
        centerY = try container.decode(Double.self, forKey: .centerY)
        radius = try container.decode(Double.self, forKey: .radius)
        instruction = try container.decode(String.self, forKey: .instruction)
        action = try container.decodeIfPresent(String.self, forKey: .action)
        avoid = try container.decodeIfPresent([String].self, forKey: .avoid) ?? []
        constraints = try container.decodeIfPresent([String].self, forKey: .constraints) ?? []
    }
}

public struct DesignOption: Codable, Hashable, Sendable {
    public var imageUrl: String
    public var caption: String

    public init(imageUrl: String, caption: String) {
        self.imageUrl = imageUrl
        self.caption = caption
    }

    enum CodingKeys: String, CodingKey {
        case imageUrl = "image_url"
        case caption
    }
}

public struct ProductMatch: Codable, Hashable, Sendable {
    public var categoryGroup: String
    public var productName: String
    public var retailer: String
    public var priceCents: Int
    public var productUrl: String
    public var imageUrl: String?
    public var confidenceScore: Double
    public var whyMatched: String
    public var fitStatus: String?
    public var fitDetail: String?
    public var dimensions: String?

    public init(
        categoryGroup: String,
        productName: String,
        retailer: String,
        priceCents: Int,
        productUrl: String,
        imageUrl: String? = nil,
        confidenceScore: Double,
        whyMatched: String,
        fitStatus: String? = nil,
        fitDetail: String? = nil,
        dimensions: String? = nil
    ) {
        self.categoryGroup = categoryGroup
        self.productName = productName
        self.retailer = retailer
        self.priceCents = priceCents
        self.productUrl = productUrl
        self.imageUrl = imageUrl
        self.confidenceScore = confidenceScore
        self.whyMatched = whyMatched
        self.fitStatus = fitStatus
        self.fitDetail = fitDetail
        self.dimensions = dimensions
    }

    enum CodingKeys: String, CodingKey {
        case categoryGroup = "category_group"
        case productName = "product_name"
        case retailer
        case priceCents = "price_cents"
        case productUrl = "product_url"
        case imageUrl = "image_url"
        case confidenceScore = "confidence_score"
        case whyMatched = "why_matched"
        case fitStatus = "fit_status"
        case fitDetail = "fit_detail"
        case dimensions
    }
}

public struct UnmatchedItem: Codable, Hashable, Sendable {
    public var category: String
    public var searchKeywords: String
    public var googleShoppingUrl: String

    public init(category: String, searchKeywords: String, googleShoppingUrl: String) {
        self.category = category
        self.searchKeywords = searchKeywords
        self.googleShoppingUrl = googleShoppingUrl
    }

    enum CodingKeys: String, CodingKey {
        case category
        case searchKeywords = "search_keywords"
        case googleShoppingUrl = "google_shopping_url"
    }
}

public struct ChatMessage: Codable, Hashable, Sendable {
    public var role: String
    public var content: String

    public init(role: String, content: String) {
        self.role = role
        self.content = content
    }
}

public struct QuickReplyOption: Codable, Hashable, Identifiable, Sendable {
    public var number: Int
    public var label: String
    public var value: String

    public var id: Int { number }

    public init(number: Int, label: String, value: String) {
        self.number = number
        self.label = label
        self.value = value
    }
}

public struct WorkflowError: Codable, Hashable, Sendable {
    public var message: String
    public var retryable: Bool

    public init(message: String, retryable: Bool) {
        self.message = message
        self.retryable = retryable
    }
}

public struct RevisionRecord: Codable, Hashable, Sendable {
    public var revisionNumber: Int
    public var type: String
    public var baseImageUrl: String
    public var revisedImageUrl: String
    public var instructions: [String]

    /// Type-safe revision type accessor.
    public var revisionTypeEnum: RevisionType? { RevisionType(rawValue: type) }

    public init(
        revisionNumber: Int,
        type: String,
        baseImageUrl: String,
        revisedImageUrl: String,
        instructions: [String] = []
    ) {
        self.revisionNumber = revisionNumber
        self.type = type
        self.baseImageUrl = baseImageUrl
        self.revisedImageUrl = revisedImageUrl
        self.instructions = instructions
    }

    enum CodingKeys: String, CodingKey {
        case revisionNumber = "revision_number"
        case type
        case baseImageUrl = "base_image_url"
        case revisedImageUrl = "revised_image_url"
        case instructions
    }
}

// MARK: - Photo / Scan Data

public struct PhotoData: Codable, Hashable, Identifiable, Sendable {
    public var photoId: String
    public var storageKey: String
    public var photoType: String
    public var note: String?

    public var id: String { photoId }

    /// Type-safe photo type accessor (mirrors `WorkflowState.projectStep` pattern).
    public var photoTypeEnum: PhotoType? { PhotoType(rawValue: photoType) }

    public init(photoId: String, storageKey: String, photoType: String, note: String? = nil) {
        self.photoId = photoId
        self.storageKey = storageKey
        self.photoType = photoType
        self.note = note
    }

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case storageKey = "storage_key"
        case photoType = "photo_type"
        case note
    }
}

public struct ScanData: Codable, Hashable, Sendable {
    public var storageKey: String
    public var roomDimensions: RoomDimensions?

    public init(storageKey: String, roomDimensions: RoomDimensions? = nil) {
        self.storageKey = storageKey
        self.roomDimensions = roomDimensions
    }

    enum CodingKeys: String, CodingKey {
        case storageKey = "storage_key"
        case roomDimensions = "room_dimensions"
    }
}

// MARK: - Shopping List Output

public struct ShoppingListOutput: Codable, Hashable, Sendable {
    public var items: [ProductMatch]
    public var unmatched: [UnmatchedItem]
    public var totalEstimatedCostCents: Int

    public init(
        items: [ProductMatch],
        unmatched: [UnmatchedItem] = [],
        totalEstimatedCostCents: Int
    ) {
        self.items = items
        self.unmatched = unmatched
        self.totalEstimatedCostCents = totalEstimatedCostCents
    }

    enum CodingKeys: String, CodingKey {
        case items, unmatched
        case totalEstimatedCostCents = "total_estimated_cost_cents"
    }
}

// MARK: - Workflow State (the big one — drives all navigation)

public struct WorkflowState: Codable, Hashable, Sendable {
    public var step: String
    public var photos: [PhotoData]
    public var scanData: ScanData?
    public var designBrief: DesignBrief?
    public var generatedOptions: [DesignOption]
    public var selectedOption: Int?
    public var currentImage: String?
    public var revisionHistory: [RevisionRecord]
    public var iterationCount: Int
    public var shoppingList: ShoppingListOutput?
    public var approved: Bool
    public var error: WorkflowError?
    public var chatHistoryKey: String?

    public init(
        step: String = "photos",
        photos: [PhotoData] = [],
        scanData: ScanData? = nil,
        designBrief: DesignBrief? = nil,
        generatedOptions: [DesignOption] = [],
        selectedOption: Int? = nil,
        currentImage: String? = nil,
        revisionHistory: [RevisionRecord] = [],
        iterationCount: Int = 0,
        shoppingList: ShoppingListOutput? = nil,
        approved: Bool = false,
        error: WorkflowError? = nil,
        chatHistoryKey: String? = nil
    ) {
        self.step = step
        self.photos = photos
        self.scanData = scanData
        self.designBrief = designBrief
        self.generatedOptions = generatedOptions
        self.selectedOption = selectedOption
        self.currentImage = currentImage
        self.revisionHistory = revisionHistory
        self.iterationCount = iterationCount
        self.shoppingList = shoppingList
        self.approved = approved
        self.error = error
        self.chatHistoryKey = chatHistoryKey
    }

    enum CodingKeys: String, CodingKey {
        case step, photos, approved, error
        case scanData = "scan_data"
        case designBrief = "design_brief"
        case generatedOptions = "generated_options"
        case selectedOption = "selected_option"
        case currentImage = "current_image"
        case revisionHistory = "revision_history"
        case iterationCount = "iteration_count"
        case shoppingList = "shopping_list"
        case chatHistoryKey = "chat_history_key"
    }

    /// Type-safe step accessor
    public var projectStep: ProjectStep? {
        ProjectStep(rawValue: step)
    }
}

// MARK: - API Request Models

public struct CreateProjectRequest: Codable, Sendable {
    public var deviceFingerprint: String
    public var hasLidar: Bool

    public init(deviceFingerprint: String, hasLidar: Bool = false) {
        self.deviceFingerprint = deviceFingerprint
        self.hasLidar = hasLidar
    }

    enum CodingKeys: String, CodingKey {
        case deviceFingerprint = "device_fingerprint"
        case hasLidar = "has_lidar"
    }
}

public struct CreateProjectResponse: Codable, Sendable {
    public var projectId: String

    enum CodingKeys: String, CodingKey {
        case projectId = "project_id"
    }
}

public struct PhotoUploadResponse: Codable, Sendable {
    public var photoId: String
    public var validation: ValidatePhotoOutput

    public init(photoId: String, validation: ValidatePhotoOutput) {
        self.photoId = photoId
        self.validation = validation
    }

    enum CodingKeys: String, CodingKey {
        case photoId = "photo_id"
        case validation
    }
}

public struct ValidatePhotoOutput: Codable, Hashable, Sendable {
    public var passed: Bool
    public var failures: [String]
    public var messages: [String]

    public init(passed: Bool, failures: [String] = [], messages: [String] = []) {
        self.passed = passed
        self.failures = failures
        self.messages = messages
    }
}

public struct IntakeStartRequest: Codable, Sendable {
    public var mode: String

    public init(mode: String) {
        self.mode = mode
    }
}

public struct IntakeMessageRequest: Codable, Sendable {
    public var message: String
    public var conversationHistory: [ChatMessage]?
    public var mode: String?

    enum CodingKeys: String, CodingKey {
        case message
        case conversationHistory = "conversation_history"
        case mode
    }

    public init(message: String, conversationHistory: [ChatMessage]? = nil, mode: String? = nil) {
        self.message = message
        self.conversationHistory = conversationHistory
        self.mode = mode
    }
}

public struct IntakeConfirmRequest: Codable, Sendable {
    public var brief: DesignBrief

    public init(brief: DesignBrief) {
        self.brief = brief
    }
}

public struct SelectOptionRequest: Codable, Sendable {
    public var index: Int

    public init(index: Int) {
        self.index = index
    }
}

public struct AnnotationEditRequest: Codable, Sendable {
    public var annotations: [AnnotationRegion]

    public init(annotations: [AnnotationRegion]) {
        self.annotations = annotations
    }
}

public struct TextFeedbackRequest: Codable, Sendable {
    public var feedback: String

    public init(feedback: String) {
        self.feedback = feedback
    }
}

public struct IntakeChatOutput: Codable, Sendable {
    public var agentMessage: String
    public var options: [QuickReplyOption]?
    public var isOpenEnded: Bool
    public var progress: String?
    public var isSummary: Bool
    public var partialBrief: DesignBrief?

    public init(
        agentMessage: String,
        options: [QuickReplyOption]? = nil,
        isOpenEnded: Bool = false,
        progress: String? = nil,
        isSummary: Bool = false,
        partialBrief: DesignBrief? = nil
    ) {
        self.agentMessage = agentMessage
        self.options = options
        self.isOpenEnded = isOpenEnded
        self.progress = progress
        self.isSummary = isSummary
        self.partialBrief = partialBrief
    }

    enum CodingKeys: String, CodingKey {
        case agentMessage = "agent_message"
        case options
        case isOpenEnded = "is_open_ended"
        case progress
        case isSummary = "is_summary"
        case partialBrief = "partial_brief"
    }
}

public struct ActionResponse: Codable, Sendable {
    public var status: String

    public init(status: String = "ok") {
        self.status = status
    }
}

public struct ErrorResponse: Codable, Sendable {
    public var error: String
    public var message: String
    public var retryable: Bool
    public var detail: String?
    /// X-Request-ID from the backend response header. Not decoded from JSON —
    /// set by RealWorkflowClient after parsing the HTTP response.
    public var requestId: String?

    private enum CodingKeys: String, CodingKey {
        case error, message, retryable, detail
    }

    public init(error: String, message: String, retryable: Bool, detail: String? = nil, requestId: String? = nil) {
        self.error = error
        self.message = message
        self.retryable = retryable
        self.detail = detail
        self.requestId = requestId
    }
}
