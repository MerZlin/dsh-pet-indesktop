"""Offline distribution trust uses approved records, not bundle-supplied keys."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from scripts.feature_release_signing import ReleaseSigningError, create_encrypted_key


def generated(tmp_path):
    key = tmp_path / "key.pem"
    record = create_encrypted_key(key, b"generated-password-only", repository_root=tmp_path / "repository", key_id="release-2026")
    root = tmp_path / "artifacts"
    root.mkdir()
    artifacts = {
        "core-setup": "Core-Setup.exe",
        "core-zip": "Core.zip",
        "portable-zip": "Core-Portable.zip",
        "ai-dlc": "AI.zip",
        "screen-dlc": "Screen.zip",
    }
    for name in artifacts.values():
        (root / name).write_bytes(b"generated artifact only")
    return key, record, root, artifacts


def test_public_policy_round_trip_rejects_invalid_fingerprint(tmp_path):
    from scripts.release_distribution import read_public_policy, write_public_policy

    key, record, root, artifacts = generated(tmp_path)
    policy = tmp_path / "approved-policy.json"
    write_public_policy(policy, [record])
    assert read_public_policy(policy) == {record.key_id: record}
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["keys"][0]["fingerprint"] = "0" * 64
    policy.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseSigningError, match="invalid_public_policy"):
        read_public_policy(policy)


def test_distribution_round_trip_uses_external_trust_and_complete_artifact_inventory(tmp_path):
    from scripts.release_distribution import sign_distribution, verify_distribution

    key, record, root, artifacts = generated(tmp_path)
    sign_distribution(root, artifacts, key, b"generated-password-only", record, release_version="5.0.0")
    manifest = verify_distribution(root, {record.key_id: record})
    assert set(manifest["artifacts"]) == set(artifacts)
    assert all(item["size"] == 23 for item in manifest["artifacts"].values())
    assert "public_key" not in manifest
    with pytest.raises(ReleaseSigningError, match="distribution_key_untrusted"):
        verify_distribution(root, {})


@pytest.mark.parametrize("mutation", ["payload", "manifest", "revoked"])
def test_distribution_mutations_are_refused(tmp_path, mutation):
    from scripts.release_distribution import sign_distribution, verify_distribution

    key, record, root, artifacts = generated(tmp_path)
    sign_distribution(root, artifacts, key, b"generated-password-only", record, release_version="5.0.0")
    trusted = record
    if mutation == "payload":
        (root / artifacts["ai-dlc"]).write_bytes(b"tampered")
    elif mutation == "manifest":
        (root / "distribution.json").write_bytes(b"{}")
    else:
        trusted = replace(record, revoked=True)
    with pytest.raises(ReleaseSigningError):
        verify_distribution(root, {trusted.key_id: trusted})


def test_distribution_does_not_overwrite_or_sign_paths_outside_delivery_root(tmp_path):
    from scripts.release_distribution import sign_distribution

    key, record, root, artifacts = generated(tmp_path)
    bad = dict(artifacts, **{"ai-dlc": "../key.pem"})
    with pytest.raises(ReleaseSigningError):
        sign_distribution(root, bad, key, b"generated-password-only", record, release_version="5.0.0")
    assert not (root / "distribution.sig").exists()
    sign_distribution(root, artifacts, key, b"generated-password-only", record, release_version="5.0.0")
    before = (root / "distribution.json").read_bytes()
    with pytest.raises(ReleaseSigningError, match="distribution_exists"):
        sign_distribution(root, artifacts, key, b"generated-password-only", record, release_version="5.0.0")
    assert (root / "distribution.json").read_bytes() == before


def test_distribution_requires_all_five_products_and_no_unknown_payload(tmp_path):
    from scripts.release_distribution import sign_distribution

    key, record, root, artifacts = generated(tmp_path)
    with pytest.raises(ReleaseSigningError, match="invalid_artifact_inventory"):
        sign_distribution(root, {"core-zip": artifacts["core-zip"]}, key, b"generated-password-only", record, release_version="5.0.0")
    (root / "private.pem").write_text("generated unrelated data", encoding="utf-8")
    with pytest.raises(ReleaseSigningError, match="unlisted_distribution_files"):
        sign_distribution(root, artifacts, key, b"generated-password-only", record, release_version="5.0.0")
    assert not (root / "distribution.sig").exists()
