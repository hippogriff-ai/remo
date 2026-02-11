import XCTest
@testable import RemoModels

final class ModelsTests: XCTestCase {

    // MARK: - JSON Decoding (mirrors backend responses)

    func testWorkflowStateDecoding() throws {
        let json = """
        {
            "step": "photos",
            "photos": [],
            "scan_data": null,
            "design_brief": null,
            "generated_options": [],
            "selected_option": null,
            "current_image": null,
            "revision_history": [],
            "iteration_count": 0,
            "shopping_list": null,
            "approved": false,
            "error": null,
            "chat_history_key": null
        }
        """.data(using: .utf8)!

        let state = try JSONDecoder().decode(WorkflowState.self, from: json)
        XCTAssertEqual(state.step, "photos")
        XCTAssertEqual(state.projectStep, .photoUpload)
        XCTAssertTrue(state.photos.isEmpty)
        XCTAssertFalse(state.approved)
    }

    func testWorkflowStateWithPhotosDecoding() throws {
        let json = """
        {
            "step": "scan",
            "photos": [
                {
                    "photo_id": "abc-123",
                    "storage_key": "projects/p1/photos/room_0.jpg",
                    "photo_type": "room",
                    "note": null
                },
                {
                    "photo_id": "def-456",
                    "storage_key": "projects/p1/photos/room_1.jpg",
                    "photo_type": "room",
                    "note": null
                }
            ],
            "scan_data": null,
            "design_brief": null,
            "generated_options": [],
            "selected_option": null,
            "current_image": null,
            "revision_history": [],
            "iteration_count": 0,
            "shopping_list": null,
            "approved": false,
            "error": null,
            "chat_history_key": null
        }
        """.data(using: .utf8)!

        let state = try JSONDecoder().decode(WorkflowState.self, from: json)
        XCTAssertEqual(state.projectStep, .scan)
        XCTAssertEqual(state.photos.count, 2)
        XCTAssertEqual(state.photos[0].photoType, "room")
    }

    func testDesignBriefDecoding() throws {
        let json = """
        {
            "room_type": "living room",
            "occupants": "couple, no kids",
            "pain_points": ["old couch", "bad lighting"],
            "keep_items": ["bookshelf"],
            "style_profile": {
                "lighting": "warm",
                "colors": ["beige", "navy"],
                "textures": ["velvet"],
                "clutter_level": "minimal",
                "mood": "cozy"
            },
            "constraints": ["budget under $5k"],
            "inspiration_notes": [
                {"photo_index": 0, "note": "love the rug", "agent_clarification": null}
            ]
        }
        """.data(using: .utf8)!

        let brief = try JSONDecoder().decode(DesignBrief.self, from: json)
        XCTAssertEqual(brief.roomType, "living room")
        XCTAssertEqual(brief.painPoints.count, 2)
        XCTAssertEqual(brief.styleProfile?.lighting, "warm")
        XCTAssertEqual(brief.styleProfile?.colors, ["beige", "navy"])
    }

    func testAnnotationRegionDecoding() throws {
        let json = """
        {"region_id": 1, "center_x": 0.5, "center_y": 0.3, "radius": 0.1, "instruction": "Replace this lamp with a modern floor lamp"}
        """.data(using: .utf8)!

        let region = try JSONDecoder().decode(AnnotationRegion.self, from: json)
        XCTAssertEqual(region.regionId, 1)
        XCTAssertEqual(region.centerX, 0.5)
        XCTAssertEqual(region.instruction, "Replace this lamp with a modern floor lamp")
    }

    func testShoppingListDecoding() throws {
        let json = """
        {
            "items": [
                {
                    "category_group": "Furniture",
                    "product_name": "Accent Chair",
                    "retailer": "West Elm",
                    "price_cents": 24999,
                    "product_url": "https://example.com/chair",
                    "image_url": null,
                    "confidence_score": 0.92,
                    "why_matched": "Style match",
                    "fit_status": "fits",
                    "fit_detail": null,
                    "dimensions": "32\\"W x 28\\"D"
                }
            ],
            "unmatched": [
                {
                    "category": "Rug",
                    "search_keywords": "modern rug 5x7",
                    "google_shopping_url": "https://google.com/search?q=rug"
                }
            ],
            "total_estimated_cost_cents": 24999
        }
        """.data(using: .utf8)!

        let shopping = try JSONDecoder().decode(ShoppingListOutput.self, from: json)
        XCTAssertEqual(shopping.items.count, 1)
        XCTAssertEqual(shopping.items[0].priceCents, 24999)
        XCTAssertEqual(shopping.items[0].fitStatus, "fits")
        XCTAssertEqual(shopping.unmatched.count, 1)
        XCTAssertEqual(shopping.totalEstimatedCostCents, 24999)
    }

    func testIntakeChatOutputDecoding() throws {
        let json = """
        {
            "agent_message": "What room type?",
            "options": [
                {"number": 1, "label": "Living Room", "value": "living room"},
                {"number": 2, "label": "Bedroom", "value": "bedroom"}
            ],
            "is_open_ended": false,
            "progress": "Question 1 of 3",
            "is_summary": false,
            "partial_brief": null
        }
        """.data(using: .utf8)!

        let output = try JSONDecoder().decode(IntakeChatOutput.self, from: json)
        XCTAssertEqual(output.agentMessage, "What room type?")
        XCTAssertEqual(output.options?.count, 2)
        XCTAssertFalse(output.isOpenEnded)
        XCTAssertEqual(output.progress, "Question 1 of 3")
    }

    func testErrorResponseDecoding() throws {
        let json = """
        {"error": "wrong_step", "message": "Cannot upload in step 'intake'", "retryable": false, "detail": null}
        """.data(using: .utf8)!

        let response = try JSONDecoder().decode(ErrorResponse.self, from: json)
        XCTAssertEqual(response.error, "wrong_step")
        XCTAssertFalse(response.retryable)
    }

    // MARK: - JSON Encoding (for request bodies)

    func testCreateProjectRequestEncoding() throws {
        let request = CreateProjectRequest(deviceFingerprint: "abc-123", hasLidar: true)
        let data = try JSONEncoder().encode(request)
        let dict = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        XCTAssertEqual(dict["device_fingerprint"] as? String, "abc-123")
        XCTAssertEqual(dict["has_lidar"] as? Bool, true)
    }

    func testAnnotationEditRequestEncoding() throws {
        let request = AnnotationEditRequest(annotations: [
            AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.3, radius: 0.1, instruction: "Replace the lamp with something modern"),
        ])
        let data = try JSONEncoder().encode(request)
        let dict = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        let annotations = dict["annotations"] as! [[String: Any]]
        XCTAssertEqual(annotations.count, 1)
        XCTAssertEqual(annotations[0]["region_id"] as? Int, 1)
    }

    // MARK: - ProjectStep

    func testProjectStepRawValues() {
        XCTAssertEqual(ProjectStep.photoUpload.rawValue, "photos")
        XCTAssertEqual(ProjectStep.scan.rawValue, "scan")
        XCTAssertEqual(ProjectStep.intake.rawValue, "intake")
        XCTAssertEqual(ProjectStep.generation.rawValue, "generation")
        XCTAssertEqual(ProjectStep.selection.rawValue, "selection")
        XCTAssertEqual(ProjectStep.iteration.rawValue, "iteration")
        XCTAssertEqual(ProjectStep.approval.rawValue, "approval")
        XCTAssertEqual(ProjectStep.shopping.rawValue, "shopping")
        XCTAssertEqual(ProjectStep.completed.rawValue, "completed")
    }

    func testProjectStepFromString() {
        XCTAssertEqual(ProjectStep(rawValue: "photos"), .photoUpload)
        XCTAssertEqual(ProjectStep(rawValue: "completed"), .completed)
        XCTAssertNil(ProjectStep(rawValue: "invalid"))
    }

    // MARK: - ProjectState.apply()

    func testApplyUpdatesStep() {
        let state = ProjectState()
        XCTAssertEqual(state.step, .photoUpload)

        let workflow = WorkflowState(step: "iteration", currentImage: "https://example.com/img.png", iterationCount: 3)
        state.apply(workflow)

        XCTAssertEqual(state.step, .iteration)
        XCTAssertEqual(state.iterationCount, 3)
        XCTAssertEqual(state.currentImage, "https://example.com/img.png")
    }

    func testApplyKeepsStepOnUnknownString() {
        let state = ProjectState()
        state.step = .selection

        let workflow = WorkflowState(step: "unknown_future_step")
        state.apply(workflow)

        // Unknown step should NOT change the current step
        XCTAssertEqual(state.step, .selection)
    }

    func testApplyUpdatesError() {
        let state = ProjectState()
        XCTAssertNil(state.error)

        let workflow = WorkflowState(
            step: "generation",
            error: WorkflowError(message: "Gemini API failed", retryable: true)
        )
        state.apply(workflow)

        XCTAssertEqual(state.error?.message, "Gemini API failed")
        XCTAssertTrue(state.error?.retryable == true)
    }

    func testApplyClearsError() {
        let state = ProjectState()
        state.error = WorkflowError(message: "old error", retryable: false)

        let workflow = WorkflowState(step: "generation")
        state.apply(workflow)

        XCTAssertNil(state.error)
    }

    // MARK: - ProjectState.preview()

    func testPreviewFactoryPhotoUpload() {
        let state = ProjectState.preview(step: .photoUpload)
        XCTAssertEqual(state.step, .photoUpload)
        XCTAssertEqual(state.projectId, "preview-1")
    }

    func testPreviewFactoryCompleted() {
        let state = ProjectState.preview(step: .completed)
        XCTAssertEqual(state.step, .completed)
        XCTAssertNotNil(state.currentImage)
        XCTAssertNotNil(state.shoppingList)
        XCTAssertFalse(state.revisionHistory.isEmpty)
    }

    func testPreviewFactorySelection() {
        let state = ProjectState.preview(step: .selection)
        XCTAssertEqual(state.generatedOptions.count, 2)
    }

    // MARK: - ProjectState computed properties

    func testRoomPhotoCount() {
        let state = ProjectState()
        state.photos = [
            PhotoData(photoId: "r1", storageKey: "k", photoType: "room"),
            PhotoData(photoId: "r2", storageKey: "k", photoType: "room"),
            PhotoData(photoId: "i1", storageKey: "k", photoType: "inspiration"),
        ]
        XCTAssertEqual(state.roomPhotoCount, 2)
        XCTAssertEqual(state.inspirationPhotoCount, 1)
    }

    // MARK: - AnyCodable

    func testAnyCodableEqualityIntegers() {
        let a = AnyCodable(42)
        let b = AnyCodable(42)
        XCTAssertEqual(a, b)
    }

    func testAnyCodableEqualityStrings() {
        let a = AnyCodable("hello")
        let b = AnyCodable("hello")
        let c = AnyCodable("world")
        XCTAssertEqual(a, b)
        XCTAssertNotEqual(a, c)
    }

    func testAnyCodableEqualityDicts() {
        let a = AnyCodable(["key": "value"])
        let b = AnyCodable(["key": "value"])
        XCTAssertEqual(a, b)
    }

    func testAnyCodableRoundTrip() throws {
        let original = AnyCodable(["width": 4.2, "name": "wall_1"])
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(AnyCodable.self, from: data)
        XCTAssertEqual(original, decoded)
    }
}
