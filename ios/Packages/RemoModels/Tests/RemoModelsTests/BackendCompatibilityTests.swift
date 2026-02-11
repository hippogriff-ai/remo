import XCTest
@testable import RemoModels

/// Tests that decode the exact JSON the backend mock API produces.
/// This ensures Swift models stay in sync with backend/app/models/contracts.py.
final class BackendCompatibilityTests: XCTestCase {

    /// Full WorkflowState at "completed" step — the most complex response.
    func testDecodeFullCompletedState() throws {
        let json = """
        {
            "step": "completed",
            "photos": [
                {"photo_id": "a1", "storage_key": "projects/p1/photos/room_0.jpg", "photo_type": "room", "note": null},
                {"photo_id": "a2", "storage_key": "projects/p1/photos/room_1.jpg", "photo_type": "room", "note": null},
                {"photo_id": "a3", "storage_key": "projects/p1/photos/inspiration_0.jpg", "photo_type": "inspiration", "note": null}
            ],
            "scan_data": {
                "storage_key": "projects/p1/lidar/scan.json",
                "room_dimensions": {
                    "width_m": 4.2,
                    "length_m": 5.8,
                    "height_m": 2.7,
                    "walls": [],
                    "openings": []
                }
            },
            "design_brief": {
                "room_type": "living room",
                "occupants": null,
                "pain_points": [],
                "keep_items": [],
                "style_profile": null,
                "constraints": [],
                "inspiration_notes": []
            },
            "generated_options": [
                {"image_url": "https://r2.example.com/projects/p1/generated/option_0.png", "caption": "Modern Minimalist"},
                {"image_url": "https://r2.example.com/projects/p1/generated/option_1.png", "caption": "Warm Contemporary"}
            ],
            "selected_option": 0,
            "current_image": "https://r2.example.com/projects/p1/generated/revision_1.png",
            "revision_history": [
                {
                    "revision_number": 1,
                    "type": "annotation",
                    "base_image_url": "https://r2.example.com/projects/p1/generated/option_0.png",
                    "revised_image_url": "https://r2.example.com/projects/p1/generated/revision_1.png",
                    "instructions": ["Replace this lamp with a modern floor lamp"]
                }
            ],
            "iteration_count": 1,
            "shopping_list": {
                "items": [
                    {
                        "category_group": "Furniture",
                        "product_name": "Mock Accent Chair",
                        "retailer": "Mock Store",
                        "price_cents": 24999,
                        "product_url": "https://example.com/accent-chair",
                        "image_url": "https://example.com/images/accent-chair.jpg",
                        "confidence_score": 0.92,
                        "why_matched": "Matches modern minimalist style",
                        "fit_status": "may_not_fit",
                        "fit_detail": "Measure doorway width before ordering",
                        "dimensions": "32\\"W x 28\\"D x 31\\"H"
                    },
                    {
                        "category_group": "Lighting",
                        "product_name": "Mock Floor Lamp",
                        "retailer": "Mock Store",
                        "price_cents": 8999,
                        "product_url": "https://example.com/floor-lamp",
                        "image_url": null,
                        "confidence_score": 0.85,
                        "why_matched": "Complements room ambiance",
                        "fit_status": null,
                        "fit_detail": null,
                        "dimensions": null
                    }
                ],
                "unmatched": [
                    {
                        "category": "Rug",
                        "search_keywords": "modern geometric area rug 5x7",
                        "google_shopping_url": "https://www.google.com/search?tbm=shop&q=modern+geometric+rug+5x7"
                    }
                ],
                "total_estimated_cost_cents": 33998
            },
            "approved": true,
            "error": null,
            "chat_history_key": "chat/p1/history.json"
        }
        """.data(using: .utf8)!

        let state = try JSONDecoder().decode(WorkflowState.self, from: json)

        // Step
        XCTAssertEqual(state.projectStep, .completed)

        // Photos
        XCTAssertEqual(state.photos.count, 3)
        XCTAssertEqual(state.photos.filter { $0.photoTypeEnum == .room }.count, 2)
        XCTAssertEqual(state.photos.filter { $0.photoTypeEnum == .inspiration }.count, 1)

        // Scan
        XCTAssertNotNil(state.scanData)
        XCTAssertEqual(state.scanData?.roomDimensions?.widthM, 4.2)
        XCTAssertEqual(state.scanData?.roomDimensions?.lengthM, 5.8)

        // Brief
        XCTAssertEqual(state.designBrief?.roomType, "living room")

        // Options
        XCTAssertEqual(state.generatedOptions.count, 2)
        XCTAssertEqual(state.generatedOptions[0].caption, "Modern Minimalist")

        // Selection & iteration
        XCTAssertEqual(state.selectedOption, 0)
        XCTAssertNotNil(state.currentImage)
        XCTAssertEqual(state.iterationCount, 1)
        XCTAssertEqual(state.revisionHistory.count, 1)
        XCTAssertEqual(state.revisionHistory[0].type, "annotation")
        XCTAssertEqual(state.revisionHistory[0].revisionTypeEnum, .annotation)

        // Shopping list
        XCTAssertNotNil(state.shoppingList)
        XCTAssertEqual(state.shoppingList?.items.count, 2)
        XCTAssertEqual(state.shoppingList?.items[0].priceCents, 24999)
        XCTAssertEqual(state.shoppingList?.items[0].fitStatus, "may_not_fit")
        XCTAssertNil(state.shoppingList?.items[1].fitStatus)
        XCTAssertEqual(state.shoppingList?.unmatched.count, 1)
        XCTAssertEqual(state.shoppingList?.totalEstimatedCostCents, 33998)

        // Flags
        XCTAssertTrue(state.approved)
        XCTAssertNil(state.error)
        XCTAssertEqual(state.chatHistoryKey, "chat/p1/history.json")
    }

    /// Test that WorkflowState with error decodes correctly.
    func testDecodeStateWithError() throws {
        let json = """
        {
            "step": "generation",
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
            "error": {"message": "Gemini API rate limited", "retryable": true},
            "chat_history_key": null
        }
        """.data(using: .utf8)!

        let state = try JSONDecoder().decode(WorkflowState.self, from: json)
        XCTAssertEqual(state.projectStep, .generation)
        XCTAssertNotNil(state.error)
        XCTAssertEqual(state.error?.message, "Gemini API rate limited")
        XCTAssertTrue(state.error?.retryable == true)
    }

    /// Test intake chat output with partial brief — the most complex intake response.
    func testDecodeIntakeSummaryResponse() throws {
        let json = """
        {
            "agent_message": "Here's what I've gathered: a living room redesign.",
            "options": null,
            "is_open_ended": false,
            "progress": "Summary",
            "is_summary": true,
            "partial_brief": {
                "room_type": "living room",
                "occupants": "couple, no kids",
                "pain_points": ["old couch", "poor lighting"],
                "keep_items": ["bookshelf", "coffee table"],
                "style_profile": {
                    "lighting": "warm",
                    "colors": ["cream", "sage"],
                    "textures": ["linen", "wood"],
                    "clutter_level": "minimal",
                    "mood": "serene"
                },
                "constraints": ["budget under $3000"],
                "inspiration_notes": [
                    {"photo_index": 0, "note": "Love the neutral palette", "agent_clarification": "Warm beige tones with natural wood accents"}
                ]
            }
        }
        """.data(using: .utf8)!

        let output = try JSONDecoder().decode(IntakeChatOutput.self, from: json)
        XCTAssertTrue(output.isSummary)
        XCTAssertNotNil(output.partialBrief)
        XCTAssertEqual(output.partialBrief?.roomType, "living room")
        XCTAssertEqual(output.partialBrief?.painPoints, ["old couch", "poor lighting"])
        XCTAssertEqual(output.partialBrief?.styleProfile?.mood, "serene")
        XCTAssertEqual(output.partialBrief?.inspirationNotes.count, 1)
        XCTAssertEqual(output.partialBrief?.inspirationNotes[0].agentClarification, "Warm beige tones with natural wood accents")
    }

    /// Test photo upload response — the shape returned by POST /projects/{id}/photos.
    func testDecodePhotoUploadResponse() throws {
        let json = """
        {
            "photo_id": "abc-123",
            "validation": {
                "passed": false,
                "failures": ["blurry", "too_dark"],
                "messages": ["Photo is too blurry. Please retake.", "Photo is too dark. Try better lighting."]
            }
        }
        """.data(using: .utf8)!

        let response = try JSONDecoder().decode(PhotoUploadResponse.self, from: json)
        XCTAssertEqual(response.photoId, "abc-123")
        XCTAssertFalse(response.validation.passed)
        XCTAssertEqual(response.validation.failures, ["blurry", "too_dark"])
        XCTAssertEqual(response.validation.messages.count, 2)
    }

    // MARK: - Request Encoding (iOS → Backend)

    /// CreateProjectRequest must encode with snake_case keys.
    func testEncodeCreateProjectRequest() throws {
        let request = CreateProjectRequest(deviceFingerprint: "iphone-15-abc", hasLidar: true)
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(dict["device_fingerprint"] as? String, "iphone-15-abc")
        XCTAssertEqual(dict["has_lidar"] as? Bool, true)
        // Verify snake_case keys (not camelCase)
        XCTAssertNil(dict["deviceFingerprint"])
        XCTAssertNil(dict["hasLidar"])
    }

    /// IntakeStartRequest must use "mode" key.
    func testEncodeIntakeStartRequest() throws {
        let request = IntakeStartRequest(mode: "quick")
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(dict["mode"] as? String, "quick")
    }

    /// IntakeConfirmRequest must encode the brief with snake_case keys.
    func testEncodeIntakeConfirmRequest() throws {
        let brief = DesignBrief(
            roomType: "living room",
            painPoints: ["bad lighting"],
            styleProfile: StyleProfile(lighting: "warm", colors: ["cream"])
        )
        let request = IntakeConfirmRequest(brief: brief)
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        // Top-level has "brief" key
        let briefDict = try XCTUnwrap(dict["brief"] as? [String: Any])
        XCTAssertEqual(briefDict["room_type"] as? String, "living room")
        XCTAssertEqual(briefDict["pain_points"] as? [String], ["bad lighting"])
        // Nested style_profile
        let style = try XCTUnwrap(briefDict["style_profile"] as? [String: Any])
        XCTAssertEqual(style["lighting"] as? String, "warm")
    }

    /// AnnotationEditRequest must encode annotations with snake_case keys.
    func testEncodeAnnotationEditRequest() throws {
        let region = AnnotationRegion(regionId: 1, centerX: 0.5, centerY: 0.3, radius: 0.1, instruction: "Replace this lamp")
        let request = AnnotationEditRequest(annotations: [region])
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let annotations = try XCTUnwrap(dict["annotations"] as? [[String: Any]])
        XCTAssertEqual(annotations.count, 1)
        XCTAssertEqual(annotations[0]["region_id"] as? Int, 1)
        XCTAssertEqual(annotations[0]["center_x"] as? Double, 0.5)
        XCTAssertEqual(annotations[0]["center_y"] as? Double, 0.3)
        // Verify snake_case (not camelCase)
        XCTAssertNil(annotations[0]["regionId"])
        XCTAssertNil(annotations[0]["centerX"])
    }

    /// SelectOptionRequest encodes index correctly.
    func testEncodeSelectOptionRequest() throws {
        let request = SelectOptionRequest(index: 1)
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(dict["index"] as? Int, 1)
    }

    /// TextFeedbackRequest encodes feedback correctly.
    func testEncodeTextFeedbackRequest() throws {
        let request = TextFeedbackRequest(feedback: "Make the couch darker")
        let data = try JSONEncoder().encode(request)
        let dict = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(dict["feedback"] as? String, "Make the couch darker")
    }

    // MARK: - Typed Enum Accessors

    /// PhotoData.photoTypeEnum returns correct enum values.
    func testPhotoDataTypedAccessor() {
        let room = PhotoData(photoId: "1", storageKey: "k", photoType: "room")
        let inspo = PhotoData(photoId: "2", storageKey: "k", photoType: "inspiration")
        let unknown = PhotoData(photoId: "3", storageKey: "k", photoType: "panorama")

        XCTAssertEqual(room.photoTypeEnum, .room)
        XCTAssertEqual(inspo.photoTypeEnum, .inspiration)
        XCTAssertNil(unknown.photoTypeEnum) // forward compat: unknown values return nil
    }

    /// RevisionRecord.revisionTypeEnum returns correct enum values.
    func testRevisionRecordTypedAccessor() {
        let ann = RevisionRecord(revisionNumber: 1, type: "annotation", baseImageUrl: "a", revisedImageUrl: "b")
        let fb = RevisionRecord(revisionNumber: 2, type: "feedback", baseImageUrl: "a", revisedImageUrl: "b")
        let unknown = RevisionRecord(revisionNumber: 3, type: "ai_auto", baseImageUrl: "a", revisedImageUrl: "b")

        XCTAssertEqual(ann.revisionTypeEnum, .annotation)
        XCTAssertEqual(fb.revisionTypeEnum, .feedback)
        XCTAssertNil(unknown.revisionTypeEnum)
    }
}
