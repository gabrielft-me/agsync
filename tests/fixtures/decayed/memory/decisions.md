# Decisions

## D-013 — compileSdk bumped to 36
- **Date:** 2026-08-06
- **Decision:** Target SDK 36.

## D-021 — Ink strokes render on a dedicated in-canvas layer
- **Date:** 2026-08-07
- **Decision:** Strokes get their own layer.

## D-021 — App shell is ReactNative + Expo
- **Date:** 2026-08-08
- **Decision:** RN + Expo for the shell.

## D-022 — Default backend model
- **Date:** 2026-08-06
- **Decision:** TUTOR_MODEL defaults to opus.
- **Rationale:** High-res vision.

## D-025 — Model downsized to Sonnet 4.6 (amends D-022)
- **Date:** 2026-08-07
- **Decision:** `TUTOR_MODEL` default changes to sonnet, by explicit user
  direction (cost/latency).
- **Consequences:** Amends D-022's rationale. If task 18's IoU acceptance
  fails on Sonnet, retry on Opus.

## D-027 — Error marks are advisory (supersedes D-019)
- **Decision:** Marks never block the user.
