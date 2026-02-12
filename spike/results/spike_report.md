# Gemini Quality Spike Report

Date: 2026-02-11 15:07:42

## gemini-3-pro-image-preview

**4/4 passed**

- [PASS] **initial_generation** (17.5s): Image generated. Size: (1024, 1024). Text: 
  - Scores: {'has_image': True, 'size': '(1024, 1024)'}
- [PASS] **annotation_editing** (21.2s): Edit generated. Sigs: 1. Text: 
  - Scores: {'has_image': True, 'thought_signatures': 1}
- [PASS] **chat_roundtrip** (30.8s): Round-trip succeeded. History: 6.02MB. Turn 2 image generated.
  - Scores: {'history_size_mb': 6.02, 'thought_signatures_turn1': 1}
- [PASS] **text_only_edit** (28.6s): Text-only edit succeeded. Size: (1024, 1024)

## gemini-2.5-flash-image

**4/4 passed**

- [PASS] **initial_generation** (9.0s): Image generated. Size: (1024, 1024). Text: 
  - Scores: {'has_image': True, 'size': '(1024, 1024)'}
- [PASS] **annotation_editing** (6.8s): Edit generated. Sigs: 1. Text: 
  - Scores: {'has_image': True, 'thought_signatures': 1}
- [PASS] **chat_roundtrip** (15.6s): Round-trip succeeded. History: 1.62MB. Turn 2 image generated.
  - Scores: {'history_size_mb': 1.62, 'thought_signatures_turn1': 1}
- [PASS] **text_only_edit** (17.4s): Text-only edit succeeded. Size: (1024, 1024)
