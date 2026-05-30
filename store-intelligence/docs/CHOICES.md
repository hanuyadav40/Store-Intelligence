# Technical Decisions — Store Intelligence Platform

## Decision 1: Detection Model — YOLOv8n vs alternatives

**Problem:** Need fast, accurate person detection on CPU (no GPU assumed in deployment).

**Options considered:**
| Option | Accuracy | CPU Speed | Complexity |
|--------|----------|-----------|------------|
| YOLOv8n | Good (mAP 37.3) | ~45ms/frame | Low — one pip install |
| YOLOv5s | Good | ~50ms/frame | Similar |
| Detectron2 | High | ~300ms/frame | High — separate build |
| OpenPose | Medium (keypoints only) | ~150ms/frame | Medium |

**AI suggestion reviewed:** Use YOLOv8n because it ships ByteTrack integration out of the box (`model.track(tracker='bytetrack.yaml')`), eliminating the need to write a custom SORT/ByteTrack integration. The `ultralytics` package includes all tracker config files.

**Final choice:** YOLOv8n. Rationale: ByteTrack is built-in, the model is ~6MB, single-dependency install, and performance on persons in controlled retail lighting is well-documented.

---

## Decision 2: Event Schema Design — Flat vs Nested

**Problem:** Design the event schema for the ingest endpoint. Events need to carry zone, dwell, staff, and billing metadata.

**Options considered:**
| Option | Pros | Cons |
|--------|------|------|
| Flat schema (all fields at top level) | Simple SQL storage, no JSON parsing | Many nullable fields, schema changes add columns |
| Nested `metadata` object | Extension-friendly, billing-specific fields in metadata | Slightly harder to index |
| Separate event type tables | Type-safe, no nulls | Complex routing, harder ingest |

**AI suggestion reviewed:** Use a **hybrid** — put core routing fields flat (store_id, camera_id, visitor_id, event_type, timestamp, zone_id, dwell_ms, is_staff, confidence) and put event-type-specific extensions in a `metadata` JSONB object (queue_depth, sku_zone, session_seq). This balances queryability of core fields with extensibility for new event types.

**Final choice:** Hybrid schema adopted. Core fields are indexed columns; metadata is stored as JSONB. This meant the Pydantic schema could enforce `queue_depth` presence for `BILLING_QUEUE_JOIN` at the model layer without adding optional columns to the events table.

---

## Decision 3: Re-ID Strategy — Neural vs Traditional Features

**Problem:** Identify returning visitors across sessions (re-entry detection) without relying on GPU or large model downloads.

**Options considered:**
| Option | Accuracy | Dependencies | Inference time |
|--------|----------|--------------|----------------|
| OSNet / FastReID | High | PyTorch + pretrained weights (100MB+) | ~10ms/GPU, ~200ms/CPU |
| Person re-id via embedding | High | Same as above | Similar |
| HOG + colour histogram | Medium | OpenCV only | ~2ms/CPU |
| Colour histogram only | Low | OpenCV only | <1ms/CPU |

**AI suggestion reviewed:** Use HOG + colour histogram with cosine similarity. For retail CCTV where customers wear varied clothing and sessions are separated by minutes (not hours), traditional appearance features achieve sufficient discriminative power. The cosine similarity threshold (default 0.72) should be tuned per store environment.

**Final choice:** HOG + colour histogram. The 0.72 threshold was selected to balance precision (avoid false re-entries from similar-looking customers) against recall (catch genuine re-entries). This keeps the pipeline dependency-free from large model weights and enables `docker compose up` to work on any machine in < 2 minutes.
