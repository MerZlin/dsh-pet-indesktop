"""Reproducible, GUI-free frozen probe build contract."""

from pathlib import Path

import pytest


def test_probe_bootloader_has_explicit_crt_entry_and_no_gui_linkage(tmp_path):
    from scripts.build_feature_probe_native import bootloader_command

    command = bootloader_command("gcc", tmp_path, tmp_path / "probe.exe")
    assert "-mwindows" in command and "-municode" in command
    assert any("--entry,WinMainCRTStartup" in arg for arg in command)
    assert not any(lib in command for lib in ("-luser32", "-lgdi32", "-lcomctl32", "-lole32"))
    assert "-DHAVE_STDBOOL_H" in command


def test_probe_bootloader_source_is_exactly_pinned(tmp_path):
    from scripts.build_feature_probe_native import extract_source

    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"not the pinned upstream distribution")
    with pytest.raises(ValueError, match="source digest"):
        extract_source(archive, tmp_path / "source")
    assert not (tmp_path / "source").exists()


def test_probe_build_rejects_existing_output_before_any_compile(tmp_path):
    from scripts.build_feature_probe_native import build

    with pytest.raises(FileExistsError):
        build(tmp_path, archive=Path("missing"), compiler="gcc")


def test_probe_runtime_removes_only_common_controls_manifest_dependency():
    from scripts.build_feature_probe import minimal_runtime_manifest

    manifest = b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><dependency><dependentAssembly><assemblyIdentity name="Microsoft.Windows.Common-Controls" version="6.0.0.0"/></dependentAssembly></dependency><application xmlns="urn:schemas-microsoft-com:asm.v3"><windowsSettings><longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware></windowsSettings></application></assembly>'
    result = minimal_runtime_manifest(manifest)
    assert b"Common-Controls" not in result
    assert b"longPathAware" in result
    assert b"true" in result


def test_probe_runtime_rejects_unknown_manifest_dependencies():
    from scripts.build_feature_probe import minimal_runtime_manifest

    with pytest.raises(ValueError, match="unexpected runtime dependency"):
        minimal_runtime_manifest(
            b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1"><dependency><dependentAssembly><assemblyIdentity name="Unknown"/></dependentAssembly></dependency></assembly>'
        )


def test_runtime_policy_records_complete_activation_resource_removal():
    import hashlib

    from scripts.build_feature_probe import runtime_manifest_removal_policy

    manifest = b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><dependency><dependentAssembly><assemblyIdentity name="Microsoft.Windows.Common-Controls" version="6.0.0.0"/></dependentAssembly></dependency></assembly>'
    policy = runtime_manifest_removal_policy(manifest)
    assert policy["manifest_sha256"] == hashlib.sha256(manifest).hexdigest()
    assert policy["resource_removed"] is True
    assert "whole" in policy["change"]


def test_runtime_resource_editor_requires_explicit_owned_root(tmp_path):
    from scripts.build_feature_probe import patch_owned_python_runtime

    with pytest.raises(ValueError, match="outside owned"):
        patch_owned_python_runtime(tmp_path / "outside/python311.dll", owned_root=tmp_path / "owned")


def test_network_canary_reports_initialization_denial_without_skipping_other_checks(monkeypatch):
    import builtins

    from scripts.feature_probe_canary import network_access

    original = builtins.__import__

    def denied(name, *args, **kwargs):
        if name == "socket":
            raise ImportError("WSAStartup failed: error code 10107")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", denied)
    allowed, detail = network_access(2, ("127.0.0.1", 1234))
    assert not allowed
    assert detail == {"stage": "initialize", "error": "ImportError"}


def test_probe_crypto_wheel_requires_pinned_digest_before_extraction(tmp_path):
    from scripts.build_feature_probe import stage_headless_crypto

    wheel = tmp_path / "forged.whl"
    wheel.write_bytes(b"attacker-provided replacement")
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match="crypto archive digest"):
        stage_headless_crypto(wheel, output)
    assert not output.exists()


def test_empty_third_party_activation_manifest_must_be_exact_empty_template():
    from scripts.build_feature_probe import empty_activation_resource_policy

    valid = b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"></assembly>'
    assert empty_activation_resource_policy(valid)["resource_removed"] is True
    with pytest.raises(ValueError):
        empty_activation_resource_policy(b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1"><dependency/></assembly>')


def test_runtime_component_policy_checks_upstream_content_not_growing_name_list(tmp_path):
    from scripts.build_feature_probe import owned_runtime_kind

    base = tmp_path / "python"
    (base / "DLLs").mkdir(parents=True)
    (base / "DLLs/pyexpat.pyd").write_bytes(b"trusted CPython copy")
    copy = tmp_path / "owned" / "pyexpat.pyd"
    copy.parent.mkdir()
    copy.write_bytes(b"trusted CPython copy")
    assert owned_runtime_kind(copy, python_root=base) == "cpython"
    copy.write_bytes(b"same-name replacement")
    assert owned_runtime_kind(copy, python_root=base) is None
    unrelated = copy.with_name("_imaging.pyd")
    unrelated.write_bytes(b"third party")
    assert owned_runtime_kind(unrelated, python_root=base) is None


def test_helper_build_contract_collects_native_crypto_backend_explicitly():
    from scripts.build_feature_probe import headless_crypto_arguments

    arguments = headless_crypto_arguments()
    assert "--hidden-import" not in arguments
    assert "_cffi_backend" in arguments and "nacl" in arguments


def test_owned_executable_declares_long_paths_without_gui_dependencies(tmp_path):
    import hashlib
    import shutil
    import sys
    import xml.etree.ElementTree as ET

    if sys.platform != "win32":
        pytest.skip("native executable resource contract")
    import pefile
    import PyInstaller

    from scripts.build_feature_probe import write_owned_executable_manifest

    original = Path(PyInstaller.__file__).parent / "bootloader/Windows-64bit-intel/run.exe"
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    owned = tmp_path / "owned"
    owned.mkdir()
    executable = owned / "probe.exe"
    shutil.copyfile(original, executable)
    write_owned_executable_manifest(executable, owned_root=owned)
    pe = pefile.PE(str(executable))
    manifests = []
    for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if kind.id == 24:
            for name in kind.directory.entries:
                for lang in name.directory.entries:
                    data = lang.data.struct
                    manifests.append((name.id, pe.get_data(data.OffsetToData, data.Size)))
    pe.close()
    assert len(manifests) == 1 and manifests[0][0] == 1
    root = ET.fromstring(manifests[0][1])
    setting = root.find(".//{http://schemas.microsoft.com/SMI/2016/WindowsSettings}longPathAware")
    assert setting is not None and setting.text == "true"
    assert root.find("{urn:schemas-microsoft-com:asm.v1}dependency") is None
    assert b"Common-Controls" not in manifests[0][1]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError, match="outside owned"):
        write_owned_executable_manifest(executable, owned_root=tmp_path / "other")


def test_native_network_canary_does_not_depend_on_python_socket_initialization(monkeypatch):
    import builtins
    import sys
    from types import SimpleNamespace

    from scripts.feature_probe_canary import native_network_access

    original = builtins.__import__
    calls = []

    def denied_socket(name, *args, **kwargs):
        if name == "socket":
            raise ImportError("generated Python socket initialization denial")
        return original(name, *args, **kwargs)

    def probe(family, host, port):
        calls.append((family, host, port))
        return {"allowed": True, "stage": "connect", "error": 0, "connect_attempted": True}

    monkeypatch.setattr(builtins, "__import__", denied_socket)
    monkeypatch.setitem(sys.modules, "_dsh_probe_native", SimpleNamespace(network_connect=probe))
    allowed, detail = native_network_access(2, ["127.0.0.1", 43210])
    assert allowed and calls == [(2, "127.0.0.1", 43210)]
    assert detail == {"api": "windows.winsock2", "stage": "connect", "error": 0, "connect_attempted": True}


def test_native_network_initialization_denial_is_not_misreported_as_connect_denial(monkeypatch):
    import sys
    from types import SimpleNamespace

    from scripts.feature_probe_canary import native_network_access

    monkeypatch.setitem(
        sys.modules,
        "_dsh_probe_native",
        SimpleNamespace(
            network_connect=lambda *args: {
                "allowed": False,
                "stage": "initialize",
                "error": 10107,
                "connect_attempted": False,
            }
        ),
    )
    allowed, detail = native_network_access(23, ["::1", 43210])
    assert not allowed and detail["api"] == "windows.winsock2"
    assert detail["stage"] == "initialize" and detail["error"] == 10107
    assert detail["connect_attempted"] is False


def test_network_permission_gate_rejects_loader_failure_as_network_denial():
    from scripts.validate_feature_probe_windows import check_network_results

    positive = dict.fromkeys(("ipv4", "ipv6"), {"api": "windows.winsock2", "stage": "connect", "error": 0, "connect_attempted": True})
    negative = dict.fromkeys(("ipv4", "ipv6"), {"api": "windows.winsock2", "stage": "load", "error": 126, "connect_attempted": False})
    with pytest.raises(AssertionError, match="network boundary not reached"):
        check_network_results(positive, negative)
    initialize_denied = dict.fromkeys(("ipv4", "ipv6"), {"api": "windows.winsock2", "stage": "initialize", "error": 10107, "connect_attempted": False})
    check_network_results(positive, initialize_denied)


@pytest.mark.parametrize("module", ["feature_probe_canary", "validate_feature_probe_windows"])
def test_public_canary_contract_import_does_not_require_windows_registry(module, monkeypatch):
    import builtins
    import importlib.util

    original = builtins.__import__

    def no_winreg(name, *args, **kwargs):
        if name == "winreg":
            raise ModuleNotFoundError("generated non-Windows registry boundary")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_winreg)
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_generated_registry_free_contract", root / "scripts" / (module + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    assert callable(getattr(loaded, "network_access" if module == "feature_probe_canary" else "check_network_results"))


def test_headless_bootloader_long_paths_are_explicit_and_fail_closed():
    from scripts.build_feature_probe_native import patch_long_path_loading

    path_source = "    return _wfopen(wfilename, wmode);"
    home_source = """    /* Macro to avoid manual code repetition. */
    #define _IMPL_CASE(PY_VERSION, PY_FLAGS, PYCONFIG_IMPL) \
    case _MAKE_VERSION_ID(PY_VERSION, PY_FLAGS): { \
        PYCONFIG_IMPL *config_impl = (PYCONFIG_IMPL *)config; \
        return _pyi_pyconfig_set_string(config, &config_impl->home, pyi_ctx->application_home_dir, dylib_python); \
    }"""
    path_result, home_result = patch_long_path_loading(path_source, home_source)
    assert "extended_filename" in path_result
    assert "wfilename[1] == L':'" in path_result
    assert "home_path" in home_result
    assert "application_home_dir[1] == ':'" in home_result
    assert "stdlib_dir" not in home_result  # no replacement Python/library discovery
    with pytest.raises(ValueError, match="long-path loading seam"):
        patch_long_path_loading(path_source + path_source, home_source)
    with pytest.raises(ValueError, match="long-path loading seam"):
        patch_long_path_loading(path_source, home_source.replace("&config_impl->home", "&config_impl->other"))


def test_headless_runtime_paths_are_explicit_before_python_bootstrap():
    from scripts.build_feature_probe_native import patch_explicit_runtime_paths

    main = "    return _pyi_resolve_executable_win32(pyi_ctx->executable_filename);"
    config = "    /* Set */\n    ret = _pyi_pyconfig_set_module_search_paths("
    patched_main, patched_config = patch_explicit_runtime_paths(main, config)
    assert "extended_executable" in patched_main
    assert "executable_filename[1] == ':'" in patched_main
    assert '"Py_SetPath"' in patched_config
    assert "module_search_paths_w[i]" in patched_config
    assert "dylib_python->version != 311" in patched_config
    assert "PATHCCH_FORCE" not in patched_config
    for broken_main, broken_config in ((main + main, config), (main, config + config)):
        with pytest.raises(ValueError, match="explicit runtime path seam"):
            patch_explicit_runtime_paths(broken_main, broken_config)
