"""Offline operator entry: passwords only in a trusted local masked dialog.

Run as ``python -m scripts.feature_release_cli`` from the repository. Never
pass a password via argv, environment or chat. Real key creation requires the
operator's separate target/impact authorization before launching this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QTextEdit, QVBoxLayout

from pet import feature_state_io as io
from pet.official_features import OFFICIAL_FEATURES, official_feature
from pet.plugins.package_trust import FeaturePackageVerifier, VerificationLimits
from scripts.feature_release_signing import ReleaseSigningError, create_encrypted_key, sign_package_snapshot, verify_key_backup
from scripts.release_distribution import read_public_policy, sign_distribution, verify_distribution, write_public_policy


class ReleasePasswordDialog(QDialog):
    """No application profile, network, logging, secret persistence or echo."""

    def __init__(self, purpose: str, target: Path, *, confirm=False):
        super().__init__()
        self.setWindowTitle("离线发布签名 · 本地密码")
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        title = QLabel("创建加密 Ed25519 私钥" if confirm else "解锁明确选择的签名私钥")
        layout.addWidget(title)
        details = QTextEdit(self)
        details.setReadOnly(True)
        details.setAcceptRichText(False)
        details.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        details.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details.setAccessibleName("本次离线操作的目标和确认摘要")
        details.setAccessibleDescription("只读可滚动；请核对各包来源、目标、版本和摘要，不包含密码。")
        details.setPlainText(f"操作：{purpose}\n目标：{target}\n不会覆盖现有文件。密码只在此本地窗口输入，不进入终端、日志或聊天。")
        layout.addWidget(details, 1)
        form = QFormLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("私钥密码")
        self.password.setAccessibleDescription("仅用于本次离线解锁，不保存。创建时至少 12 个 UTF-8 字节。")
        form.addRow("私钥密码", self.password)
        self.confirmation = QLineEdit(self)
        self.confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirmation.setAccessibleName("再次输入密码")
        if confirm:
            form.addRow("再次输入", self.confirmation)
        else:
            self.confirmation.hide()
        self._confirm = confirm
        layout.addLayout(form)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认目标并创建" if confirm else "解锁本次操作")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.password.setFocus()
        if confirm:
            QDialog.setTabOrder(self.password, self.confirmation)
            QDialog.setTabOrder(self.confirmation, buttons.button(QDialogButtonBox.StandardButton.Ok))

    def accept(self):
        password = self.password.text().encode("utf-8")
        if not password or (self._confirm and (len(password) < 12 or self.password.text() != self.confirmation.text())):
            self.error.setText("密码不能为空；创建时须至少 12 个字节且两次输入一致。")
            return
        super().accept()

    def reject(self):
        self.password.clear()
        self.confirmation.clear()
        super().reject()

    def take_password(self):
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        value = self.password.text().encode("utf-8")
        self.password.clear()
        self.confirmation.clear()
        return value


def _prompt(purpose, target, *, confirm=False):
    app = QApplication.instance() or QApplication([])
    dialog = ReleasePasswordDialog(purpose, target, confirm=confirm)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.take_password()
    finally:
        dialog.deleteLater()
        app.processEvents()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown argv; even an accidentally supplied
        # password must not be copied into tool output or diagnostics.
        raise ReleaseSigningError("invalid_arguments")


def _parser():
    parser = _Parser(description="Offline official signing; password via local dialog only")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    create = commands.add_parser("create-key")
    for flag in ("key", "key-id", "repository", "public-policy"):
        create.add_argument("--" + flag, required=True)
    for command in ("backup-check", "sign-package", "sign-packages", "sign-distribution", "verify-distribution"):
        sub = commands.add_parser(command)
        sub.add_argument("--public-policy", required=True)
        sub.add_argument("--key-id", required=True)
        sub.add_argument("--approved-key-fingerprint", required=True, help="SHA-256 independently approved out of band")
        if command != "verify-distribution":
            sub.add_argument("--key", required=True)
        if command == "sign-package":
            sub.add_argument("--source", required=True)
            sub.add_argument("--destination", required=True)
            sub.add_argument("--core-version", required=True)
        if command == "sign-packages":
            sub.add_argument("--package", action="append", nargs=2, required=True, metavar=("SOURCE", "DESTINATION"))
            sub.add_argument("--core-version", required=True)
        if command in {"sign-distribution", "verify-distribution"}:
            sub.add_argument("--root", required=True)
        if command == "sign-distribution":
            sub.add_argument("--inventory", required=True)
            sub.add_argument("--release-version", required=True)
    return parser


def _public(record):
    value = asdict(record)
    value["feature_ids"] = sorted(record.feature_ids)
    value["capabilities"] = sorted(record.capabilities)
    return json.dumps(value, sort_keys=True)


def _batch_targets(args, record):
    """Public immutable inputs; no private-key access, code imports or writes."""
    if len(args.package) != len(OFFICIAL_FEATURES) or record.revoked:
        raise ReleaseSigningError("batch_owner_set_invalid")
    rows, owners = [], set()
    roots: list[Path] = []
    for source, destination in args.package:
        source, destination = Path(source).absolute(), Path(destination).absolute()
        io.safe_path(source)
        io.safe_path(destination)
        if destination.exists():
            raise ReleaseSigningError("batch_target_exists")
        raw = io.read_bytes(source / "manifest.json", VerificationLimits().max_manifest_bytes)
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or manifest.get("format_version") != 2 or manifest.get("key_id") != record.key_id:
            raise ReleaseSigningError("invalid_release_manifest")
        owner = manifest.get("id")
        if not isinstance(owner, str):
            raise ReleaseSigningError("invalid_release_manifest")
        feature = official_feature(owner)
        if feature.id in owners or feature.id not in record.feature_ids or not feature.capabilities <= record.capabilities:
            raise ReleaseSigningError("batch_owner_set_invalid")
        FeaturePackageVerifier(core_version=args.core_version, api_version="1", feature_id=feature.id, allowed_capabilities=feature.capabilities)._schema(raw)
        owners.add(feature.id)
        roots.extend((source.resolve(), destination.resolve()))
        rows.append((feature.id, manifest["version"], source, destination, hashlib.sha256(raw).hexdigest()))
    if owners != set(OFFICIAL_FEATURES):
        raise ReleaseSigningError("batch_owner_set_invalid")
    for index, root in enumerate(roots):
        if any(root.is_relative_to(other) or other.is_relative_to(root) for other in roots[index + 1 :]):
            raise ReleaseSigningError("unsafe_snapshot_target")
    return rows


def run_cli(argv=None, *, prompt=None, write=print):
    prompt = prompt or _prompt
    try:
        args = _parser().parse_args(argv)
        if args.command == "create-key":
            key, policy = Path(args.key).absolute(), Path(args.public_policy).absolute()
            io.safe_path(key)
            io.safe_path(policy)
            if key.exists() or policy.exists():
                raise ReleaseSigningError("key_or_policy_exists")
            password = prompt(args.command, key, confirm=True)
            if password is None:
                write("cancelled")
                return 1
            record = create_encrypted_key(key, password, repository_root=Path(args.repository), key_id=args.key_id)
            write_public_policy(policy, [record])
            write(_public(record))
            return 0
        policy_path = Path(args.public_policy).absolute()
        records = read_public_policy(policy_path)
        record = records.get(args.key_id)
        if record is None or record.fingerprint != args.approved_key_fingerprint:
            raise ReleaseSigningError("public_identity_not_approved")
        if args.command == "verify-distribution":
            root = Path(args.root).absolute()
            if policy_path.resolve().is_relative_to(root.resolve()):
                raise ReleaseSigningError("bundle_cannot_supply_its_own_trust")
            verify_distribution(root, {record.key_id: record})
            write("distribution_verified")
            return 0
        key = Path(args.key).absolute()
        batch = _batch_targets(args, record) if args.command == "sign-packages" else None
        purpose = args.command
        if batch is not None:
            purpose += "\n" + "\n".join(
                f"{owner} {version}: {source} → {destination}\nmanifest SHA-256: {digest}" for owner, version, source, destination, digest in batch
            )
            purpose += f"\n签名身份：{record.key_id}\n公开指纹 SHA-256：{record.fingerprint}"
            purpose += "\n两个包分别签名；部分失败不发布、不删除已有结果。"
        password = prompt(purpose, key)
        if password is None:
            write("cancelled")
            return 1
        if batch is not None:
            if _batch_targets(args, record) != batch:
                raise ReleaseSigningError("batch_source_changed")
            reports = []
            for owner, version, source, destination, digest in batch:
                descriptor = sign_package_snapshot(source, destination, key, password, record, core_version=args.core_version, expected_manifest_digest=digest)
                item = dict(feature_id=descriptor.id, version=descriptor.version, manifest_digest=hashlib.sha256(descriptor.raw_manifest).hexdigest())
                reports.append(item)
                write(json.dumps({"status": "package_signed", **item}))
            write(json.dumps({"status": "packages_signed", "packages": reports}))
        elif args.command == "backup-check":
            verify_key_backup(key, password, record)
            write("backup_verified")
        elif args.command == "sign-package":
            descriptor = sign_package_snapshot(Path(args.source), Path(args.destination), key, password, record, core_version=args.core_version)
            write(
                json.dumps({"feature_id": descriptor.id, "version": descriptor.version, "manifest_digest": hashlib.sha256(descriptor.raw_manifest).hexdigest()})
            )
        else:
            inventory = json.loads(io.read_bytes(Path(args.inventory).absolute(), 65536))
            sign_distribution(Path(args.root), inventory, key, password, record, release_version=args.release_version)
            write("distribution_signed_and_verified")
        return 0
    except ReleaseSigningError as exc:
        write(exc.code)
        return 2 if exc.code == "invalid_arguments" else 1
    except (OSError, io.StateError, ValueError, TypeError):
        write("release_operation_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
