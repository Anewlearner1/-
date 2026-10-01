# MLOps plan (DRAFT — nothing here has been deployed, run, or tested on real infrastructure)

Scope: v1 is offline batch (one uploaded video in, annotated result out). No real-time serving. Only the manifest code in `model_manifest.py` is executable and tested (schema + sha256 checks); everything below is design.

## a) Routing stages to compute (technical-plan §3)

| Stage | Device class | Reason |
|---|---|---|
| Pose (MediaPipe) | CPU | "low-medium" compute; runs on CPU today |
| Court keypoints, shot timing, stroke classifier, rendering | CPU | "low" compute |
| Player detection (YOLOv8, planned) | CPU first; GPU only if measured too slow | Decide from logged numbers, not assumption |
| Ball tracking (TrackNet family, planned) | GPU | technical-plan §3 says GPU needed |

Design: a batch job runs stages sequentially per video. The CPU worker runs everything except ball tracking; ball tracking is a separate queued step dispatched to a GPU worker, which can be scaled to zero between jobs (cost lever in technical-plan §7: batch scheduling). Stage-to-device mapping lives in one config table so it can change after measurement.

## b) Per-video, per-stage cost measurement

Log one record per (video, stage) run:

| Field | Meaning |
|---|---|
| video_id | job identifier |
| stage | pose / player-detection / ball-tracking / ... |
| model_name, model_version | from manifest |
| device_type | e.g. `cpu-<N>vcpu`, `gpu-<model>` (label as reported by the host) |
| wall_clock_seconds | stage end minus start, monotonic clock |
| video_seconds | duration of the input video |
| frames | frames processed |

Derived: `sec_per_frame = wall_clock_seconds / frames`; `realtime_factor = wall_clock_seconds / video_seconds`.

Cost:

```
stage_cost      = wall_clock_seconds / 3600 * PRICE_PER_HOUR[device_type]
video_cost      = sum(stage_cost over stages) + OVERHEAD_PER_VIDEO
cost_per_minute = video_cost / (video_seconds / 60)
```

Unit prices (placeholders, to be filled from the actual provider's price list; none are asserted here):
`PRICE_PER_HOUR[cpu-...] = <TBD>`, `PRICE_PER_HOUR[gpu-...] = <TBD>`, `OVERHEAD_PER_VIDEO = <TBD: storage/egress/idle>`.

Note: if a GPU is billed for startup/idle time, wall-clock of the stage understates cost; track worker billed seconds separately once a provider is chosen.

## c) Model rollout / rollback via the manifest

- `models.json` is the single source of truth: name, version, source, SPDX license, sha256, status.
- Gate to `in_use`: license verified (validator already enforces this), sha256 recorded and verified against the downloaded file, entry reviewed. Dependency licenses (e.g. `ultralytics`, torch) tracked alongside; ADR 0001 covers AGPL acceptance.
- Rollout: add the new version as `planned`, run it offline against a fixed evaluation set and compare accuracy and cost/minute, then flip status in a reviewed commit. Keep the previous version's entry (and its weights) available.
- Rollback: revert the manifest commit; workers fetch by manifest entry and verify sha256 before use. Record the model version in each job's cost log so results are traceable.
- Gap to fix: current pose URLs point at `latest`, which is not pinned; full/heavy sha256 are null. Mirror pinned copies to our own storage before relying on rollback. (`cv/pose_overlay.py` does not yet verify hashes; it is owned by another role.)

## d) Cannot be done in this sandbox

- No GPU (`nvidia-smi` absent): cannot run or benchmark TrackNet/YOLO on GPU, so no GPU timings exist.
- No cloud credentials: no provisioning, no real prices, no billing data.
- Docker CLI present but daemon unreachable: no container builds or runs; no Dockerfile was written.
- No timing data collected: the stage-cost logger above is a spec, not implemented.
- TrackNet license and weights provenance unchecked; sha256 for planned/full/heavy models not computed (files not present).
- Upstream `latest` pose files may differ from the one hashed here (lite, cached 2026-09-30).
