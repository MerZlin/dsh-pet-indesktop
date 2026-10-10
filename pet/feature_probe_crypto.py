"""Core-owned headless package verifier; NOT an alternate activation policy.

Bundled upstream libsodium is pinned by the helper build and its sealed inventory.
The helper uses the exact same FeaturePackageVerifier schema, compatibility,
path, file-inventory and payload checks. Phase5A local activation intentionally
accepts an explicitly selected folder/ZIP without publisher keys; signed package
verification remains a compatibility mode for a future formal release workflow.
The native adapter only calls libsodium when that compatibility mode is active.
"""

from pathlib import Path

from .plugins.package_trust import FeaturePackageVerifier, PackageVerificationError, _checked_stat


class HeadlessFeaturePackageVerifier(FeaturePackageVerifier):
    def __init__(self, *, snapshot_ancestors=None, **policy):
        super().__init__(**policy)
        self._snapshot_ancestors = None if snapshot_ancestors is None else tuple(tuple(item) for item in snapshot_ancestors)

    def _ancestors(self, root: Path) -> tuple[tuple[str, int, int], ...]:
        # Only for the parent-owned READONLY snapshot. Parent performs the full
        # ancestry check before launch AND before accepting the receipt. LPAC
        # deliberately has no permission on ancestors outside its owned root.
        evidence = self._snapshot_ancestors
        if evidence is None:
            return super()._ancestors(root)
        paths = (*reversed(root.parents), root)
        if not root.is_absolute() or ".." in root.parts or root.drive.startswith("\\\\") or len(evidence) != len(paths):
            raise PackageVerificationError("snapshot ancestry shape")
        for item, path in zip(evidence, paths):
            if len(item) != 3 or item[0] != str(path) or type(item[1]) is not int or type(item[2]) is not int or item[1] < 0 or item[2] < 0:
                raise PackageVerificationError("snapshot ancestry identity")
        stamp = _checked_stat(root, directory=True)
        if evidence[-1] != (str(root), stamp.device, stamp.inode):
            raise PackageVerificationError("snapshot root replaced")
        return evidence

    @staticmethod
    def _valid_signature(key: bytes, signature: bytes, raw: bytes) -> bool:
        import _dsh_probe_native

        return _dsh_probe_native.verify_ed25519(key, signature, raw) is True
