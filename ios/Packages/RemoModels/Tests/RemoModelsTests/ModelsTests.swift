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
        // Verify backward compatibility: new optional fields get defaults
        XCTAssertNil(region.action)
        XCTAssertEqual(region.avoid, [])
        XCTAssertEqual(region.constraints, [])
    }

    func testAnnotationRegionDecodingWithAllFields() throws {
        let json = """
        {"region_id": 2, "center_x": 0.3, "center_y": 0.7, "radius": 0.12, "instruction": "Remove this shelf entirely", "action": "Remove", "avoid": ["modern art", "glass"], "constraints": ["kid-friendly"]}
        """.data(using: .utf8)!

        let region = try JSONDecoder().decode(AnnotationRegion.self, from: json)
        XCTAssertEqual(region.regionId, 2)
        XCTAssertEqual(region.action, "Remove")
        XCTAssertEqual(region.avoid, ["modern art", "glass"])
        XCTAssertEqual(region.constraints, ["kid-friendly"])
    }

    func testAnnotationRegionRoundTrip() throws {
        let original = AnnotationRegion(
            regionId: 1, centerX: 0.5, centerY: 0.3, radius: 0.1,
            instruction: "Replace with floor lamp",
            action: "Replace", avoid: ["plastic"], constraints: ["budget"]
        )
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(AnnotationRegion.self, from: data)
        XCTAssertEqual(original, decoded)
    }

    func testAnnotationRegionDecodingNullAction() throws {
        // Explicit null for action should decode as nil (same as omitting)
        let json = """
        {"region_id": 1, "center_x": 0.5, "center_y": 0.3, "radius": 0.1, "instruction": "Test", "action": null, "avoid": [], "constraints": []}
        """.data(using: .utf8)!

        let region = try JSONDecoder().decode(AnnotationRegion.self, from: json)
        XCTAssertNil(region.action)
        XCTAssertEqual(region.avoid, [])
        XCTAssertEqual(region.constraints, [])
    }

    func testAnnotationRegionPartialFields() throws {
        // Action set but avoid/constraints omitted — should default to empty arrays
        let json = """
        {"region_id": 1, "center_x": 0.5, "center_y": 0.5, "radius": 0.1, "instruction": "Change color", "action": "Change finish"}
        """.data(using: .utf8)!

        let region = try JSONDecoder().decode(AnnotationRegion.self, from: json)
        XCTAssertEqual(region.action, "Change finish")
        XCTAssertEqual(region.avoid, [])
        XCTAssertEqual(region.constraints, [])
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
        // requestId is set from HTTP header, not JSON — should be nil after decode
        XCTAssertNil(response.requestId)
    }

    func testErrorResponseRequestIdNotDecodedFromJSON() throws {
        // Even if JSON contains request_id, it should be ignored (excluded from CodingKeys)
        let json = """
        {"error": "server_error", "message": "Something failed", "retryable": true, "request_id": "req-from-json"}
        """.data(using: .utf8)!

        let response = try JSONDecoder().decode(ErrorResponse.self, from: json)
        XCTAssertNil(response.requestId, "requestId should not be decoded from JSON — it's set from HTTP headers only")
    }

    // MARK: - JSON Encoding (for request bodies)

    func testCreateProjectRequestEncoding() throws {
        let request = CreateProjectRequest(deviceFingerprint: "abc-123", hasLidar: true)
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(dict["device_fingerprint"] as? String, "abc-123")
        XCTAssertEqual(dict["has_lidar"] as? Bool, true)
    }

    func testAnnotationEditRequestEncoding() throws {
        let request = AnnotationEditRequest(annotations: [
            AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.3, radius: 0.1, instruction: "Replace the lamp with something modern"),
        ])
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let annotations = try XCTUnwrap(dict["annotations"] as? [[String: Any]])
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
        XCTAssertEqual(ProjectStep.abandoned.rawValue, "abandoned")
        XCTAssertEqual(ProjectStep.cancelled.rawValue, "cancelled")
    }

    func testProjectStepFromString() {
        XCTAssertEqual(ProjectStep(rawValue: "photos"), .photoUpload)
        XCTAssertEqual(ProjectStep(rawValue: "completed"), .completed)
        XCTAssertEqual(ProjectStep(rawValue: "abandoned"), .abandoned)
        XCTAssertEqual(ProjectStep(rawValue: "cancelled"), .cancelled)
        XCTAssertNil(ProjectStep(rawValue: "invalid"))
    }

    func testProjectStepIsTerminal() {
        // Non-terminal steps
        let nonTerminal: [ProjectStep] = [.photoUpload, .scan, .analyzing, .intake, .generation, .selection, .iteration, .approval, .shopping]
        for step in nonTerminal {
            XCTAssertFalse(step.isTerminal, "\(step) should not be terminal")
        }
        // Terminal steps
        XCTAssertTrue(ProjectStep.completed.isTerminal)
        XCTAssertTrue(ProjectStep.abandoned.isTerminal)
        XCTAssertTrue(ProjectStep.cancelled.isTerminal)
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

    func testApplyTerminalStateAbandoned() {
        let state = ProjectState()
        state.step = .iteration
        let workflow = WorkflowState(step: "abandoned")
        state.apply(workflow)
        XCTAssertEqual(state.step, .abandoned)
        XCTAssertTrue(state.step.isTerminal)
    }

    func testApplyTerminalStateCancelled() {
        let state = ProjectState()
        state.step = .generation
        let workflow = WorkflowState(step: "cancelled")
        state.apply(workflow)
        XCTAssertEqual(state.step, .cancelled)
        XCTAssertTrue(state.step.isTerminal)
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

    // MARK: - ProjectStep ordering (Comparable)

    func testProjectStepOrderingFollowsWorkflow() {
        let steps: [ProjectStep] = [.photoUpload, .scan, .analyzing, .intake, .generation, .selection, .iteration, .approval, .shopping, .completed, .abandoned, .cancelled]
        // Each step should be less than the next
        for i in 0..<steps.count - 1 {
            XCTAssertTrue(steps[i] < steps[i + 1], "\(steps[i]) should be < \(steps[i + 1])")
        }
    }

    func testTerminalStepsSortAfterCompleted() {
        XCTAssertTrue(ProjectStep.completed < .abandoned)
        XCTAssertTrue(ProjectStep.completed < .cancelled)
        XCTAssertTrue(ProjectStep.abandoned < .cancelled)
    }

    func testProjectStepEqualityNotLessThan() {
        XCTAssertFalse(ProjectStep.intake < .intake)
        XCTAssertFalse(ProjectStep.completed < .completed)
    }

    func testProjectStepLateStepsNotLessThanEarly() {
        XCTAssertFalse(ProjectStep.completed < .photoUpload)
        XCTAssertFalse(ProjectStep.iteration < .scan)
    }

    func testProjectStepSortingProducesWorkflowOrder() {
        let shuffled: [ProjectStep] = [.completed, .photoUpload, .iteration, .scan, .approval]
        let sorted = shuffled.sorted()
        XCTAssertEqual(sorted, [.photoUpload, .scan, .iteration, .approval, .completed])
    }

    // MARK: - ProjectState.preview() for all steps

    func testPreviewFactoryScan() {
        let state = ProjectState.preview(step: .scan)
        XCTAssertEqual(state.step, .scan)
        XCTAssertEqual(state.photos.count, 2)
        XCTAssertEqual(state.photos[0].photoTypeEnum, .room)
    }

    func testPreviewFactoryIntake() {
        let state = ProjectState.preview(step: .intake)
        XCTAssertEqual(state.step, .intake)
        XCTAssertEqual(state.photos.count, 2)
    }

    func testPreviewFactoryGeneration() {
        let state = ProjectState.preview(step: .generation)
        XCTAssertEqual(state.step, .generation)
    }

    func testPreviewFactoryIteration() {
        let state = ProjectState.preview(step: .iteration)
        XCTAssertEqual(state.step, .iteration)
        XCTAssertNotNil(state.currentImage)
        XCTAssertEqual(state.iterationCount, 1)
    }

    func testPreviewFactoryApproval() {
        let state = ProjectState.preview(step: .approval)
        XCTAssertEqual(state.step, .approval)
        XCTAssertNotNil(state.currentImage)
        XCTAssertEqual(state.iterationCount, 2)
    }

    func testPreviewFactoryShopping() {
        let state = ProjectState.preview(step: .shopping)
        XCTAssertEqual(state.step, .shopping)
    }

    // MARK: - GenerationStatus

    func testGenerationStatusCodableRoundTrip() throws {
        let statuses: [GenerationStatus] = [.idle, .generating, .completed, .failed("API error")]
        for status in statuses {
            let data = try JSONEncoder().encode(status)
            let decoded = try JSONDecoder().decode(GenerationStatus.self, from: data)
            XCTAssertEqual(status, decoded, "Round-trip failed for \(status)")
        }
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

    func testJSONValueEqualityStrings() {
        let a: JSONValue = "hello"
        let b: JSONValue = "hello"
        let c: JSONValue = "world"
        XCTAssertEqual(a, b)
        XCTAssertNotEqual(a, c)
    }

    func testJSONValueEqualityDicts() {
        let a: JSONValue = ["key": "value"]
        let b: JSONValue = ["key": "value"]
        XCTAssertEqual(a, b)
    }

    func testJSONValueRoundTrip() throws {
        let original: JSONValue = ["width": 4.2, "name": "wall_1"]
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(original, decoded)
    }

    func testJSONValueNull() throws {
        let original: JSONValue = .null
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(original, decoded)
    }

    func testJSONValueBool() throws {
        let original: JSONValue = true
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(original, decoded)
    }

    func testJSONValueNestedStructure() throws {
        // Realistic wall data from LiDAR parser
        // Note: JSON round-trip normalizes 0.0 -> int(0) since JSON doesn't distinguish
        let original: JSONValue = [
            "start": .array([0.1, 0.2]),
            "end": .array([4.2, 3.1]),
            "height": 2.7,
            "has_window": false
        ]
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(original, decoded)
    }

    func testJSONValueArray() throws {
        let original: JSONValue = .array([1, 2, 3])
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(original, decoded)
    }

    // MARK: - RoomDimensions with non-empty walls/openings

    func testRoomDimensionsWithWallData() throws {
        let json = """
        {
            "width_m": 4.2,
            "length_m": 5.8,
            "height_m": 2.7,
            "walls": [
                {"start": [0.0, 0.0], "end": [4.2, 0.0], "height": 2.7, "has_window": false}
            ],
            "openings": [
                {"type": "door", "width": 0.9, "height": 2.1, "wall_index": 0}
            ]
        }
        """.data(using: .utf8)!

        let dims = try JSONDecoder().decode(RoomDimensions.self, from: json)
        XCTAssertEqual(dims.widthM, 4.2)
        XCTAssertEqual(dims.walls.count, 1)
        XCTAssertEqual(dims.openings.count, 1)
        // Verify nested wall data is accessible
        XCTAssertEqual(dims.walls[0]["height"], AnyCodable(2.7))
        XCTAssertEqual(dims.openings[0]["type"], AnyCodable("door"))
    }

    func testRoomDimensionsBackwardCompatMinimal() throws {
        // Old backend JSON without new fields — should decode with defaults
        let json = """
        {
            "width_m": 3.0,
            "length_m": 4.0,
            "height_m": 2.5
        }
        """.data(using: .utf8)!

        let dims = try JSONDecoder().decode(RoomDimensions.self, from: json)
        XCTAssertEqual(dims.widthM, 3.0)
        XCTAssertEqual(dims.walls, [])
        XCTAssertEqual(dims.openings, [])
        XCTAssertEqual(dims.furniture, [])
        XCTAssertEqual(dims.surfaces, [])
        XCTAssertNil(dims.floorAreaSqm)
    }

    func testRoomDimensionsWithNewFields() throws {
        let json = """
        {
            "width_m": 4.2,
            "length_m": 5.8,
            "height_m": 2.7,
            "walls": [],
            "openings": [],
            "furniture": [
                {"type": "sofa", "width": 2.1, "depth": 0.9}
            ],
            "surfaces": [
                {"type": "floor", "material": "hardwood"}
            ],
            "floor_area_sqm": 24.36
        }
        """.data(using: .utf8)!

        let dims = try JSONDecoder().decode(RoomDimensions.self, from: json)
        XCTAssertEqual(dims.furniture.count, 1)
        XCTAssertEqual(dims.furniture[0]["type"], AnyCodable("sofa"))
        XCTAssertEqual(dims.surfaces.count, 1)
        XCTAssertEqual(dims.surfaces[0]["material"], AnyCodable("hardwood"))
        XCTAssertEqual(dims.floorAreaSqm, 24.36)
    }

    func testRoomDimensionsRoundTripWithNewFields() throws {
        let dims = RoomDimensions(
            widthM: 4.0,
            lengthM: 5.0,
            heightM: 2.5,
            furniture: [["type": AnyCodable("table")]],
            surfaces: [["type": AnyCodable("floor"), "material": AnyCodable("tile")]],
            floorAreaSqm: 20.0
        )
        let data = try JSONEncoder().encode(dims)
        let decoded = try JSONDecoder().decode(RoomDimensions.self, from: data)
        XCTAssertEqual(decoded.furniture.count, 1)
        XCTAssertEqual(decoded.surfaces.count, 1)
        XCTAssertEqual(decoded.floorAreaSqm, 20.0)
    }

    // MARK: - Forward compatibility (unknown fields ignored)

    func testWorkflowStateIgnoresUnknownFields() throws {
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
            "chat_history_key": null,
            "new_future_field": "some value",
            "another_field": 42
        }
        """.data(using: .utf8)!

        let state = try JSONDecoder().decode(WorkflowState.self, from: json)
        XCTAssertEqual(state.step, "photos")
    }

    // MARK: - DesignBrief round-trip

    func testDesignBriefRoundTrip() throws {
        let original = DesignBrief(
            roomType: "living room",
            occupants: "couple",
            painPoints: ["old couch"],
            keepItems: ["bookshelf"],
            styleProfile: StyleProfile(lighting: "warm", colors: ["beige"], textures: ["velvet"], clutterLevel: "minimal", mood: "cozy"),
            constraints: ["budget under $5k"],
            inspirationNotes: [InspirationNote(photoIndex: 0, note: "love the rug")]
        )
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(DesignBrief.self, from: data)
        XCTAssertEqual(original, decoded)
    }

    // MARK: - ProjectState.apply() all fields

    func testApplySetsAllFields() {
        let state = ProjectState()
        let photos = [PhotoData(photoId: "r1", storageKey: "k", photoType: "room")]
        let options = [DesignOption(imageUrl: "https://example.com/1.png", caption: "Option 1")]
        let revisions = [RevisionRecord(revisionNumber: 1, type: "annotation", baseImageUrl: "base", revisedImageUrl: "revised")]
        let shopping = ShoppingListOutput(items: [], totalEstimatedCostCents: 0)

        let workflow = WorkflowState(
            step: "completed",
            photos: photos,
            generatedOptions: options,
            selectedOption: 0,
            currentImage: "https://example.com/final.png",
            revisionHistory: revisions,
            iterationCount: 3,
            shoppingList: shopping,
            approved: true,
            chatHistoryKey: "chat-key-123"
        )
        state.apply(workflow)

        XCTAssertEqual(state.step, .completed)
        XCTAssertEqual(state.photos.count, 1)
        XCTAssertEqual(state.generatedOptions.count, 1)
        XCTAssertEqual(state.selectedOption, 0)
        XCTAssertEqual(state.currentImage, "https://example.com/final.png")
        XCTAssertEqual(state.revisionHistory.count, 1)
        XCTAssertEqual(state.iterationCount, 3)
        XCTAssertNotNil(state.shoppingList)
        XCTAssertTrue(state.approved)
        XCTAssertEqual(state.chatHistoryKey, "chat-key-123")
    }

    // MARK: - SSE Line Parser

    func testSSEParserDeltaEvent() {
        var parser = SSELineParser()
        XCTAssertNil(parser.feed("event: delta"))
        XCTAssertNil(parser.feed("data: {\"text\": \"hello\"}"))
        let event = parser.feed("")
        if case .delta(let text) = event {
            XCTAssertEqual(text, "hello")
        } else {
            XCTFail("Expected .delta, got \(String(describing: event))")
        }
    }

    func testSSEParserDoneEvent() {
        var parser = SSELineParser()
        _ = parser.feed("event: done")
        let json = """
        {"agent_message":"Hi","options":null,"is_open_ended":true,"progress":null,"is_summary":false,"partial_brief":null}
        """
        _ = parser.feed("data: \(json)")
        let event = parser.feed("")
        if case .done(let output) = event {
            XCTAssertEqual(output.agentMessage, "Hi")
            XCTAssertTrue(output.isOpenEnded)
            XCTAssertFalse(output.isSummary)
        } else {
            XCTFail("Expected .done, got \(String(describing: event))")
        }
    }

    func testSSEParserIgnoresUnknownEvents() {
        var parser = SSELineParser()
        _ = parser.feed("event: heartbeat")
        _ = parser.feed("data: {}")
        XCTAssertNil(parser.feed(""))
    }

    func testSSEParserIgnoresMalformedDelta() {
        var parser = SSELineParser()
        _ = parser.feed("event: delta")
        _ = parser.feed("data: not-json")
        XCTAssertNil(parser.feed(""))
    }

    func testSSEParserMultipleEvents() {
        var parser = SSELineParser()
        // First delta
        _ = parser.feed("event: delta")
        _ = parser.feed("data: {\"text\": \"a\"}")
        let e1 = parser.feed("")
        if case .delta(let t) = e1 { XCTAssertEqual(t, "a") }
        else { XCTFail("Expected .delta") }

        // Second delta
        _ = parser.feed("event: delta")
        _ = parser.feed("data: {\"text\": \"b\"}")
        let e2 = parser.feed("")
        if case .delta(let t) = e2 { XCTAssertEqual(t, "b") }
        else { XCTFail("Expected .delta") }
    }

    func testSSEParserIgnoresCommentLines() {
        var parser = SSELineParser()
        _ = parser.feed(": this is a comment")
        _ = parser.feed("event: delta")
        _ = parser.feed("data: {\"text\": \"ok\"}")
        let event = parser.feed("")
        if case .delta(let text) = event {
            XCTAssertEqual(text, "ok")
        } else {
            XCTFail("Expected .delta")
        }
    }

    func testSSEParserEmptyBlockReturnsNil() {
        var parser = SSELineParser()
        // Empty line without any event/data
        XCTAssertNil(parser.feed(""))
    }

    // MARK: - Shopping SSE Line Parser

    func testShoppingSSEParserStatusEvent() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: status")
        _ = parser.feed("data: {\"phase\": \"Extracting items\", \"item_count\": 5}")
        let event = parser.feed("")
        if case .status(let phase, let itemCount) = event {
            XCTAssertEqual(phase, "Extracting items")
            XCTAssertEqual(itemCount, 5)
        } else {
            XCTFail("Expected .status, got \(String(describing: event))")
        }
    }

    func testShoppingSSEParserStatusWithoutItemCount() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: status")
        _ = parser.feed("data: {\"phase\": \"Analyzing design\"}")
        let event = parser.feed("")
        if case .status(let phase, let itemCount) = event {
            XCTAssertEqual(phase, "Analyzing design")
            XCTAssertNil(itemCount)
        } else {
            XCTFail("Expected .status, got \(String(describing: event))")
        }
    }

    func testShoppingSSEParserItemSearchEvent() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: item_search")
        _ = parser.feed("data: {\"item\": \"accent chair\", \"candidates\": 3}")
        let event = parser.feed("")
        if case .itemSearch(let name, let candidates) = event {
            XCTAssertEqual(name, "accent chair")
            XCTAssertEqual(candidates, 3)
        } else {
            XCTFail("Expected .itemSearch, got \(String(describing: event))")
        }
    }

    func testShoppingSSEParserItemSearchWithoutCandidates() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: item_search")
        _ = parser.feed("data: {\"item\": \"floor lamp\"}")
        let event = parser.feed("")
        if case .itemSearch(let name, let candidates) = event {
            XCTAssertEqual(name, "floor lamp")
            XCTAssertNil(candidates)
        } else {
            XCTFail("Expected .itemSearch, got \(String(describing: event))")
        }
    }

    func testShoppingSSEParserItemEvent() {
        var parser = ShoppingSSELineParser()
        let json = """
        {"category_group":"Furniture","product_name":"Chair","retailer":"West Elm","price_cents":24999,"product_url":"https://example.com/chair","image_url":null,"confidence_score":0.92,"why_matched":"Style match","fit_status":"fits","fit_detail":null,"dimensions":"32\\"W"}
        """
        _ = parser.feed("event: item")
        _ = parser.feed("data: \(json)")
        let event = parser.feed("")
        if case .item(let product) = event {
            XCTAssertEqual(product.productName, "Chair")
            XCTAssertEqual(product.priceCents, 24999)
            XCTAssertEqual(product.confidenceScore, 0.92)
        } else {
            XCTFail("Expected .item, got \(String(describing: event))")
        }
    }

    func testShoppingSSEParserDoneEvent() {
        var parser = ShoppingSSELineParser()
        let json = """
        {"items":[],"unmatched":[],"total_estimated_cost_cents":0}
        """
        _ = parser.feed("event: done")
        _ = parser.feed("data: \(json)")
        let event = parser.feed("")
        if case .done(let output) = event {
            XCTAssertTrue(output.items.isEmpty)
            XCTAssertEqual(output.totalEstimatedCostCents, 0)
        } else {
            XCTFail("Expected .done, got \(String(describing: event))")
        }
    }

    func testShoppingSSEParserErrorEvent() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: error")
        _ = parser.feed("data: {\"error\": \"ANTHROPIC_API_KEY not set\"}")
        let event = parser.feed("")
        if case .error(let message) = event {
            XCTAssertEqual(message, "ANTHROPIC_API_KEY not set")
        } else {
            XCTFail("Expected .error, got \(String(describing: event))")
        }
    }

    func testShoppingSSEParserIgnoresUnknownEvents() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: heartbeat")
        _ = parser.feed("data: {}")
        XCTAssertNil(parser.feed(""))
    }

    func testShoppingSSEParserMultipleEvents() {
        var parser = ShoppingSSELineParser()
        // Status event
        _ = parser.feed("event: status")
        _ = parser.feed("data: {\"phase\": \"Searching\"}")
        let e1 = parser.feed("")
        if case .status(let phase, _) = e1 { XCTAssertEqual(phase, "Searching") }
        else { XCTFail("Expected .status") }

        // Item search event
        _ = parser.feed("event: item_search")
        _ = parser.feed("data: {\"item\": \"lamp\", \"candidates\": 2}")
        let e2 = parser.feed("")
        if case .itemSearch(let name, let candidates) = e2 {
            XCTAssertEqual(name, "lamp")
            XCTAssertEqual(candidates, 2)
        } else { XCTFail("Expected .itemSearch") }
    }

    func testShoppingSSEParserMalformedData() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: status")
        _ = parser.feed("data: not-json")
        XCTAssertNil(parser.feed(""))
    }

    func testShoppingSSEParserEmptyData() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: status")
        _ = parser.feed("data: ")
        XCTAssertNil(parser.feed(""))
    }

    func testShoppingSSEParserMissingDataLine() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: status")
        // Empty line triggers parse with no data buffered
        XCTAssertNil(parser.feed(""))
    }

    func testShoppingSSEParserEmptyEventType() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed("event: ")
        _ = parser.feed("data: {\"phase\": \"test\"}")
        // Empty event type should be treated as unknown
        XCTAssertNil(parser.feed(""))
    }

    func testShoppingSSEParserCommentLinesIgnored() {
        var parser = ShoppingSSELineParser()
        _ = parser.feed(": this is a heartbeat comment")
        _ = parser.feed("event: status")
        _ = parser.feed("data: {\"phase\": \"Searching\", \"item_count\": 5}")
        let event = parser.feed("")
        if case .status(let phase, let count) = event {
            XCTAssertEqual(phase, "Searching")
            XCTAssertEqual(count, 5)
        } else {
            XCTFail("Expected .status after comment line")
        }
    }

    // MARK: - Intake SSE Parser Edge Cases

    func testSSEParserEmptyData() {
        var parser = SSELineParser()
        _ = parser.feed("event: delta")
        _ = parser.feed("data: ")
        XCTAssertNil(parser.feed(""))
    }

    func testSSEParserMissingDataLine() {
        var parser = SSELineParser()
        _ = parser.feed("event: delta")
        XCTAssertNil(parser.feed(""))
    }

    func testSSEParserCommentLinesIgnored() {
        var parser = SSELineParser()
        _ = parser.feed(": keepalive")
        _ = parser.feed("event: delta")
        _ = parser.feed("data: {\"text\": \"hello\"}")
        let event = parser.feed("")
        if case .delta(let text) = event {
            XCTAssertEqual(text, "hello")
        } else {
            XCTFail("Expected .delta after comment line")
        }
    }

    func testSSEParserErrorEvent() {
        var parser = SSELineParser()
        _ = parser.feed("event: error")
        _ = parser.feed("data: {\"error\": \"Claude rate limited\", \"retryable\": true}")
        let event = parser.feed("")
        if case .error(let message) = event {
            XCTAssertEqual(message, "Claude rate limited")
        } else {
            XCTFail("Expected .error, got \(String(describing: event))")
        }
    }

    func testSSEParserErrorAfterDeltas() {
        var parser = SSELineParser()
        // First get a delta
        _ = parser.feed("event: delta")
        _ = parser.feed("data: {\"text\": \"partial\"}")
        let e1 = parser.feed("")
        if case .delta(let t) = e1 { XCTAssertEqual(t, "partial") }
        else { XCTFail("Expected .delta") }
        // Then get an error
        _ = parser.feed("event: error")
        _ = parser.feed("data: {\"error\": \"API failed\", \"retryable\": false}")
        let e2 = parser.feed("")
        if case .error(let msg) = e2 { XCTAssertEqual(msg, "API failed") }
        else { XCTFail("Expected .error, got \(String(describing: e2))") }
    }
}
