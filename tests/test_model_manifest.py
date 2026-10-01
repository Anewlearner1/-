import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mlops import model_manifest as mm  # noqa: E402


def good_entry(**kw):
    e = {"name": "m", "role": "pose", "version": "1", "source_url": "http://x",
         "license": "Apache-2.0", "sha256": None, "status": "planned", "notes": ""}
    e.update(kw)
    return e


def test_shipped_manifest_loads_and_has_rejected_openpose():
    m = mm.load()
    op = mm.find(m, "openpose")
    assert op["status"] == "rejected"
    assert mm.find(m, "mediapipe-pose-landmarker-lite")["status"] == "in_use"


def test_valid_entry_has_no_errors():
    assert mm.validate_entry(good_entry()) == []


def test_missing_field_and_bad_enums():
    e = good_entry()
    del e["notes"]
    assert any("missing field 'notes'" in s for s in mm.validate_entry(e))
    assert mm.validate_entry(good_entry(role="bogus"))
    assert mm.validate_entry(good_entry(status="bogus"))
    assert mm.validate_entry(good_entry(sha256="abc"))


@pytest.mark.parametrize("lic", ["unverified — must check before adoption", "", None])
def test_in_use_with_unknown_license_rejected(lic):
    errs = mm.validate_entry(good_entry(status="in_use", license=lic))
    assert any("verified license" in s for s in errs)


def test_planned_with_unverified_license_allowed():
    assert mm.validate_entry(good_entry(license="unverified — must check")) == []


def test_load_raises_on_invalid(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"models": [{"name": "x"}]}')
    with pytest.raises(mm.ManifestError):
        mm.load(p)


def test_duplicate_detected():
    m = {"models": [good_entry(), copy.deepcopy(good_entry())]}
    assert any("duplicate" in s for s in mm.validate(m))


def test_sha256_verify_pass_fail_and_null(tmp_path):
    f = tmp_path / "w.bin"
    f.write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    assert mm.verify_file(good_entry(sha256=digest), f) is True
    assert mm.verify_file(good_entry(sha256="0" * 64), f) is False
    with pytest.raises(mm.ManifestError):
        mm.verify_file(good_entry(sha256=None), f)
