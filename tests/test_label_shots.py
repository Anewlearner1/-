"""Tests for labeling/label_shots.py.

These confirm the semi-automated labeling tool's plumbing -- that it calls
``detect_shots`` correctly and produces schema-valid output in the exact
shape ml/README.md specifies -- not that any real video has been labeled
(none has; see labeling/README.md). The human-correction step is stubbed
out (candidate statuses set directly, as a JSON-editing reviewer would),
not driven through the interactive CLI prompt.
"""
from __future__ import annotations

import json

import pytest

from labeling.label_shots import (
    ADDED,
    CONFIRMED,
    PENDING,
    REJECTED,
    SOURCE_BOOTSTRAPPED,
    add_candidate,
    build_candidates,
    extract_candidates,
    finalize_labels,
)
from ml.shot_timing import detect_shots
from tests.synth_pose import synth_landmark_sequence

FPS = 30.0
CONTACTS = [40, 110, 180]


def _session_path(tmp_path, save_images=False):
    seq = synth_landmark_sequence(CONTACTS, fps=FPS)
    return extract_candidates(
        "unused.mp4", tmp_path, video_id="clip_test", save_images=save_images, seq=seq
    )


def test_build_candidates_matches_detect_shots_events():
    seq = synth_landmark_sequence(CONTACTS, fps=FPS)
    result = detect_shots(seq)

    candidates = build_candidates(result)

    assert len(candidates) == len(result.events)
    for c, e in zip(candidates, result.events):
        assert c["frame"] == e.contact_frame
        assert c["time_s"] == pytest.approx(e.contact_time_s)
        assert c["wrist"] == e.wrist
        assert c["status"] == PENDING
        assert c["image"] is None


def test_extract_candidates_writes_valid_session_json(tmp_path):
    path = _session_path(tmp_path)
    assert path.name == "candidates.json"

    session = json.loads(path.read_text())
    assert session["video_id"] == "clip_test"
    assert session["fps"] == FPS
    assert session["candidate_source"] == "detect_shots"
    assert len(session["candidates"]) == len(CONTACTS)
    for c in session["candidates"]:
        assert c["status"] == PENDING


def test_finalize_refuses_while_candidates_pending(tmp_path):
    path = _session_path(tmp_path)
    with pytest.raises(ValueError, match="pending"):
        finalize_labels(path)


def test_end_to_end_bootstrap_then_stubbed_human_correction(tmp_path):
    """extract -> stub human review (confirm all, add one missed frame,
    reject none) -> finalize, and check the emitted label is schema-valid
    and matches the exact ml/README.md interface shape.
    """
    path = _session_path(tmp_path)
    session = json.loads(path.read_text())

    # Stubbed human correction: confirm every bootstrapped candidate and
    # add one contact detect_shots missed.
    for c in session["candidates"]:
        c["status"] = CONFIRMED
    add_candidate(session, 260, note="missed by detect_shots")
    path.write_text(json.dumps(session, indent=2))

    label, label_path, meta_path = finalize_labels(path, reviewer="test-reviewer")

    # Exact shape from ml/README.md: only these three keys.
    assert set(label.keys()) == {"video_id", "fps", "contact_frames"}
    assert label["video_id"] == "clip_test"
    assert label["fps"] == FPS
    assert label["contact_frames"] == sorted(CONTACTS + [260])
    assert all(isinstance(f, int) for f in label["contact_frames"])
    assert isinstance(label["fps"], float)

    assert label_path.exists()
    assert json.loads(label_path.read_text()) == label

    meta = json.loads(meta_path.read_text())
    assert meta["source"] == SOURCE_BOOTSTRAPPED
    assert meta["video_id"] == "clip_test"
    assert meta["reviewer"] == "test-reviewer"
    assert meta["n_confirmed"] == len(CONTACTS)
    assert meta["n_added_by_human"] == 1
    assert meta["n_rejected"] == 0


def test_rejected_candidates_are_excluded_from_final_frames(tmp_path):
    path = _session_path(tmp_path)
    session = json.loads(path.read_text())

    statuses = [CONFIRMED, REJECTED, CONFIRMED]
    for c, status in zip(session["candidates"], statuses):
        c["status"] = status
    path.write_text(json.dumps(session, indent=2))

    label, _, meta_path = finalize_labels(path)

    kept = [CONTACTS[0], CONTACTS[2]]
    assert label["contact_frames"] == sorted(kept)

    meta = json.loads(meta_path.read_text())
    assert meta["n_rejected"] == 1
    assert meta["n_confirmed"] == 2


def test_add_candidate_computes_time_from_fps():
    session = {"fps": 30.0, "candidates": []}
    entry = add_candidate(session, 90, note="hand-spotted")

    assert entry["frame"] == 90
    assert entry["time_s"] == pytest.approx(3.0)
    assert entry["status"] == ADDED
    assert session["candidates"] == [entry]


def test_finalize_rejects_unknown_status(tmp_path):
    path = _session_path(tmp_path)
    session = json.loads(path.read_text())
    for c in session["candidates"]:
        c["status"] = CONFIRMED
    session["candidates"][0]["status"] = "bogus"
    path.write_text(json.dumps(session, indent=2))

    with pytest.raises(ValueError, match="unknown candidate status"):
        finalize_labels(path)
