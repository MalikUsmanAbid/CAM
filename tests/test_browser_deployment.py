"""Deferred deployment recipe tests. Fake ACL adapter/inert assets, no admin API."""

import hashlib
import json

import pytest

from scripts import deploy_browser_broker
from scripts.build_browser_native import ASSETS


def inert_pe():
    data = bytearray(256)
    data[:2] = b"MZ"
    data[60:64] = (128).to_bytes(4, "little")
    data[128:134] = b"PE\0\0\x64\x86"
    return bytes(data)


def inputs(tmp_path):
    native, browser = tmp_path / "native", tmp_path / "chromium"
    native.mkdir()
    browser.mkdir()
    for name in ASSETS:
        (native / name).write_bytes(inert_pe())
    (browser / "chrome.exe").write_bytes(inert_pe())
    (browser / "resources.pak").write_bytes(b"inert fixture resources")
    native_manifest = tmp_path / "native.json"
    native_manifest.write_text(
        json.dumps(
            {
                "format": "jarvis-browser-native-source-v1",
                "architecture": "x64",
                "activation": "denied",
                "files": {
                    name: hashlib.sha256((native / name).read_bytes()).hexdigest()
                    for name in ASSETS
                },
            }
        )
    )
    browser_manifest = tmp_path / "browser.json"
    browser_manifest.write_text(
        json.dumps(
            {
                "format": "jarvis-runtime-assets-v1",
                "files": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in browser.iterdir()
                },
            }
        )
    )
    return native, native_manifest, browser, browser_manifest


def test_deployment_copies_exact_assets_remains_denied_and_limits_acl_targets(
    tmp_path, monkeypatch
):
    arguments = inputs(tmp_path)
    destination = tmp_path / "protected-fixture"
    targets = []
    monkeypatch.setattr(
        deploy_browser_broker, "machine_paths", lambda: (destination, tmp_path / "not-invoked.exe")
    )
    monkeypatch.setattr(deploy_browser_broker, "protect", lambda path, _: targets.append(path))
    receipt = deploy_browser_broker.deploy(*arguments)
    assert receipt["activation"] == "denied" and receipt["acceptance"] == "not performed"
    assert set(receipt["native_files"]) == set(ASSETS)
    assert set(receipt["browser_files"]) == {"chrome.exe", "resources.pak"}
    assert targets == [destination, destination, destination]
    assert (destination / "Browser" / "chrome.exe").read_bytes() == inert_pe()
    assert all(path.is_relative_to(tmp_path) for path in targets)


def test_existing_deployment_and_unrelated_files_are_preserved(tmp_path, monkeypatch):
    arguments = inputs(tmp_path)
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "user.txt").write_text("preserve")
    monkeypatch.setattr(
        deploy_browser_broker, "machine_paths", lambda: (destination, tmp_path / "not-invoked.exe")
    )
    with pytest.raises(ValueError, match="already exists"):
        deploy_browser_broker.deploy(*arguments)
    assert (destination / "user.txt").read_text() == "preserve"


def test_changed_native_bytes_never_create_activation_receipt(tmp_path, monkeypatch):
    arguments = inputs(tmp_path)
    (arguments[0] / ASSETS[0]).write_bytes(b"changed inert source")
    destination = tmp_path / "partial"
    monkeypatch.setattr(
        deploy_browser_broker, "machine_paths", lambda: (destination, tmp_path / "not-invoked.exe")
    )
    monkeypatch.setattr(deploy_browser_broker, "protect", lambda *_: None)
    with pytest.raises(ValueError, match="differ"):
        deploy_browser_broker.deploy(*arguments)
    assert not (destination / "deployment.json").exists()
