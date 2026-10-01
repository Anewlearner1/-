"""Model registry: load/validate models.json and verify local files by sha256.

Only the schema checks and sha256 verification here are exercised by tests
(tests/test_model_manifest.py). No download/deploy logic lives here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name("models.json")

ROLES = {"pose", "player-detection", "ball-tracking", "court-keypoints",
         "shot-timing", "stroke-classifier"}
STATUSES = {"in_use", "planned", "rejected"}
REQUIRED = ("name", "role", "version", "source_url", "license", "sha256",
            "status", "notes")
# A license is "unknown" if it is empty or flagged as unverified.
UNVERIFIED_MARKER = "unverified"


class ManifestError(ValueError):
    pass


def is_license_unknown(lic) -> bool:
    return (not isinstance(lic, str) or not lic.strip()
            or UNVERIFIED_MARKER in lic.lower())


def validate_entry(e: dict) -> list[str]:
    """Return a list of problems (empty = valid)."""
    errs = []
    label = e.get("name", "<unnamed>") if isinstance(e, dict) else "<non-dict>"
    if not isinstance(e, dict):
        return [f"{label}: entry must be an object"]
    for k in REQUIRED:
        if k not in e:
            errs.append(f"{label}: missing field '{k}'")
    if errs:
        return errs
    if e["role"] not in ROLES:
        errs.append(f"{label}: invalid role {e['role']!r}")
    if e["status"] not in STATUSES:
        errs.append(f"{label}: invalid status {e['status']!r}")
    sha = e["sha256"]
    if sha is not None and not (isinstance(sha, str) and len(sha) == 64
                                and all(c in "0123456789abcdef" for c in sha)):
        errs.append(f"{label}: sha256 must be null or 64 lowercase hex chars")
    if e["status"] == "in_use" and is_license_unknown(e["license"]):
        errs.append(f"{label}: in_use model must have a verified license")
    return errs


def validate(manifest: dict) -> list[str]:
    models = manifest.get("models") if isinstance(manifest, dict) else None
    if not isinstance(models, list):
        return ["manifest must be an object with a 'models' list"]
    errs, seen = [], set()
    for e in models:
        errs += validate_entry(e)
        key = (e.get("name"), e.get("version")) if isinstance(e, dict) else None
        if key in seen:
            errs.append(f"duplicate entry {key}")
        seen.add(key)
    return errs


def load(path: Path | str = MANIFEST_PATH) -> dict:
    """Load and validate; raises ManifestError listing all problems."""
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    errs = validate(manifest)
    if errs:
        raise ManifestError("; ".join(errs))
    return manifest


def find(manifest: dict, name: str) -> dict:
    for e in manifest["models"]:
        if e["name"] == name:
            return e
    raise KeyError(name)


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(entry: dict, path: Path | str) -> bool:
    """True iff the file's sha256 equals the manifest value.

    Raises ManifestError if the entry has no recorded sha256 (cannot verify).
    """
    if entry["sha256"] is None:
        raise ManifestError(f"{entry['name']}: no sha256 recorded; cannot verify")
    return sha256_file(path) == entry["sha256"]
