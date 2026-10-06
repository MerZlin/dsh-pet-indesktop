"""Release CLI never accepts passwords in argv/environment or prints secrets."""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit


def test_cli_cancel_does_not_create_key_or_public_policy(tmp_path):
    from scripts.feature_release_cli import run_cli

    output = []
    key, policy = tmp_path / "key.pem", tmp_path / "public.json"
    code = run_cli(
        ["create-key", "--key", str(key), "--key-id", "release-2026", "--repository", str(tmp_path / "repo"), "--public-policy", str(policy)],
        prompt=lambda *args, **kwargs: None,
        write=output.append,
    )
    assert code == 1
    assert output == ["cancelled"]
    assert not key.exists() and not policy.exists()


def test_cli_argument_errors_do_not_echo_a_secret(tmp_path):
    from scripts.feature_release_cli import run_cli

    output = []
    assert run_cli(["--password", "must-not-appear-in-output"], write=output.append) == 2
    assert output == ["invalid_arguments"]


def test_cli_creates_only_generated_encrypted_fixture_with_explicit_target_prompt(tmp_path):
    from scripts.feature_release_cli import run_cli

    seen, output = [], []

    def prompt(purpose, target, *, confirm=False):
        seen.append((purpose, target, confirm))
        return b"generated-password-only"

    key, policy = tmp_path / "key.pem", tmp_path / "public.json"
    assert (
        run_cli(
            ["create-key", "--key", str(key), "--key-id", "release-2026", "--repository", str(tmp_path / "repo"), "--public-policy", str(policy)],
            prompt=prompt,
            write=output.append,
        )
        == 0
    )
    assert seen == [("create-key", key.absolute(), True)]
    assert b"ENCRYPTED PRIVATE KEY" in key.read_bytes()
    assert all("generated-password-only" not in text for text in output)
    assert json.loads(output[-1])["key_id"] == "release-2026"
    assert policy.exists()


def test_local_password_dialog_masks_fields_and_requires_confirmation(tmp_path):
    from scripts.feature_release_cli import ReleasePasswordDialog

    app = QApplication.instance() or QApplication([])
    dialog = ReleasePasswordDialog("create-key", tmp_path / "generated.pem", confirm=True)
    try:
        assert dialog.password.echoMode() == QLineEdit.EchoMode.Password
        assert dialog.confirmation.echoMode() == QLineEdit.EchoMode.Password
        assert dialog.password.accessibleName()
        dialog.password.setText("generated-password-only")
        dialog.confirmation.setText("different-value")
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        dialog.confirmation.setText("generated-password-only")
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.take_password() == b"generated-password-only"
        assert not dialog.password.text() and not dialog.confirmation.text()
    finally:
        dialog.close()
        app.processEvents()


def test_cli_signs_actual_generated_package_and_reports_descriptor_id(tmp_path):
    from scripts.feature_release_cli import run_cli
    from scripts.feature_release_signing import create_encrypted_key
    from scripts.release_distribution import write_public_policy
    from tests.test_official_feature_contracts import signed_package

    key_path = tmp_path / "key.pem"
    record = create_encrypted_key(key_path, b"generated-password-only", repository_root=tmp_path / "repository", key_id="release-2026")
    policy = tmp_path / "public-policy.json"
    write_public_policy(policy, [record])
    source = tmp_path / "unsigned"
    signed_package(source)
    (source / "manifest.sig").unlink()
    output = []
    result = run_cli(
        [
            "sign-package",
            "--source",
            str(source),
            "--destination",
            str(tmp_path / "signed"),
            "--key",
            str(key_path),
            "--public-policy",
            str(policy),
            "--key-id",
            record.key_id,
            "--approved-key-fingerprint",
            record.fingerprint,
            "--core-version",
            "5.0.0",
        ],
        prompt=lambda *args, **kwargs: b"generated-password-only",
        write=output.append,
    )
    assert result == 0
    report = json.loads(output[-1])
    assert report["feature_id"] == "official.ai-chat"
    assert report["version"] == "1.0.0"
    assert len(report["manifest_digest"]) == 64
    assert (tmp_path / "signed/manifest.sig").stat().st_size == 64


def batch_inputs(tmp_path):
    from scripts.feature_release_signing import create_encrypted_key
    from scripts.release_distribution import write_public_policy
    from tests.test_official_feature_contracts import AI, SCREEN, signed_package

    key = tmp_path / "generated.pem"
    record = create_encrypted_key(key, b"generated-password-only", repository_root=tmp_path / "repo", key_id="release-2026")
    policy = tmp_path / "public.json"
    write_public_policy(policy, [record])
    pairs = []
    for feature_id in (AI, SCREEN):
        source = tmp_path / (feature_id + "-unsigned")
        signed_package(source, feature_id)
        (source / "manifest.sig").unlink()
        pairs.append((source, tmp_path / (feature_id + "-signed")))
    arguments = [
        "sign-packages",
        "--key",
        str(key),
        "--public-policy",
        str(policy),
        "--key-id",
        record.key_id,
        "--approved-key-fingerprint",
        record.fingerprint,
        "--core-version",
        "5.0.0",
    ]
    for source, destination in pairs:
        arguments += ["--package", str(source), str(destination)]
    return arguments, pairs


def test_batch_signing_prompts_once_for_two_explicit_owners_without_executing_code(tmp_path, monkeypatch):
    import builtins

    from scripts.feature_release_cli import run_cli

    arguments, pairs = batch_inputs(tmp_path)
    monkeypatch.setattr(builtins, "_phase5a_candidate_executed", False, raising=False)
    prompts, output = [], []

    def prompt(purpose, target):
        prompts.append((purpose, target))
        return b"generated-password-only"

    assert run_cli(arguments, prompt=prompt, write=output.append) == 0
    assert len(prompts) == 1
    assert all(str(destination) in prompts[0][0] for _, destination in pairs)
    assert not builtins._phase5a_candidate_executed
    report = json.loads(output[-1])
    assert report["status"] == "packages_signed"
    assert {item["feature_id"] for item in report["packages"]} == {"official.ai-chat", "official.screen-understanding"}
    assert all((destination / "manifest.sig").stat().st_size == 64 for _, destination in pairs)
    assert all("generated-password-only" not in line for line in output)


def test_batch_cancel_leaves_both_outputs_absent(tmp_path):
    from scripts.feature_release_cli import run_cli

    arguments, pairs = batch_inputs(tmp_path)
    output = []
    assert run_cli(arguments, prompt=lambda *args: None, write=output.append) == 1
    assert output == ["cancelled"]
    assert all(not destination.exists() for _, destination in pairs)


def test_batch_checks_existing_second_output_before_password_or_first_write(tmp_path):
    from scripts.feature_release_cli import run_cli

    arguments, pairs = batch_inputs(tmp_path)
    pairs[1][1].mkdir()

    def forbidden_prompt(*args):
        pytest.fail("public target validation must precede password input")

    output = []
    assert run_cli(arguments, prompt=forbidden_prompt, write=output.append) == 1
    assert output == ["batch_target_exists"]
    assert not pairs[0][1].exists()


def test_batch_rejects_duplicate_owner_before_password(tmp_path):
    from scripts.feature_release_cli import run_cli

    arguments, pairs = batch_inputs(tmp_path)
    arguments[-2] = str(pairs[0][0])
    output = []
    assert run_cli(arguments, prompt=lambda *args: pytest.fail("must not unlock duplicate owners"), write=output.append) == 1
    assert output == ["batch_owner_set_invalid"]
    assert all(not destination.exists() for _, destination in pairs)


def test_batch_manifest_change_during_password_input_is_rejected_before_any_write(tmp_path):
    from scripts.feature_release_cli import run_cli

    arguments, pairs = batch_inputs(tmp_path)

    def prompt(*args):
        path = pairs[1][0] / "manifest.json"
        value = json.loads(path.read_bytes())
        value["version"] = "1.0.1"
        path.write_text(json.dumps(value))
        return b"generated-password-only"

    output = []
    assert run_cli(arguments, prompt=prompt, write=output.append) == 1
    assert output == ["batch_source_changed"]
    assert all(not destination.exists() for _, destination in pairs)


def test_batch_partial_failure_retains_first_output_without_claiming_batch_success(tmp_path, monkeypatch):
    from scripts import feature_release_cli as cli
    from scripts.feature_release_signing import ReleaseSigningError

    arguments, pairs = batch_inputs(tmp_path)
    original = cli.sign_package_snapshot

    def fail_second(source, *args, **kwargs):
        if source == pairs[1][0]:
            raise ReleaseSigningError("generated_signing_failure")
        return original(source, *args, **kwargs)

    monkeypatch.setattr(cli, "sign_package_snapshot", fail_second)
    output = []
    assert cli.run_cli(arguments, prompt=lambda *args: b"generated-password-only", write=output.append) == 1
    assert json.loads(output[0])["status"] == "package_signed"
    assert output[-1] == "generated_signing_failure"
    assert not any('"status": "packages_signed"' in line for line in output)
    assert (pairs[0][1] / "manifest.sig").exists() and not pairs[1][1].exists()


@pytest.mark.parametrize("width", [720, 1100])
def test_batch_password_dialog_keeps_long_targets_scrollable_and_password_actions_reachable(tmp_path, width):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QDialogButtonBox, QTextEdit

    from scripts.feature_release_cli import ReleasePasswordDialog

    app = QApplication.instance() or QApplication([])
    purpose = "sign-packages\n" + "official.ai-chat 1.0.0: 中文长目录/" * 160
    dialog = ReleasePasswordDialog(purpose, tmp_path / "generated.pem")
    try:
        font = QFont(dialog.font())
        font.setPointSize(18)
        dialog.setFont(font)
        dialog.resize(width, 500)
        dialog.show()
        app.processEvents()
        details = dialog.findChild(QTextEdit)
        assert details is not None, "batch targets need a bounded scrollable plain-text view"
        assert details.isReadOnly() and not details.acceptRichText()
        assert purpose in details.toPlainText()
        assert details.accessibleName() and details.accessibleDescription()
        assert details.verticalScrollBar().maximum() > 0
        assert details.horizontalScrollBar().maximum() == 0
        assert dialog.width() == width and dialog.height() == 500
        buttons = dialog.findChild(QDialogButtonBox)
        for widget in (dialog.password, buttons.button(QDialogButtonBox.StandardButton.Ok), buttons.button(QDialogButtonBox.StandardButton.Cancel)):
            assert widget.isVisible()
            assert dialog.rect().contains(widget.geometry().bottomRight())
        dialog.password.setText("generated-test-only")
        dialog.reject()
        assert dialog.take_password() is None and not dialog.password.text()
    finally:
        dialog.close()
        app.processEvents()
