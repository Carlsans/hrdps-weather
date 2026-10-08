"""Updater: signatures, verification, how each kind of install applies an update, throttling, opt-in."""
import hashlib
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

from hrdps_weather import ed25519, update

SEED = bytes(range(32))
PUB = ed25519.public_key(SEED)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_CACHE_HOME", "cache"), ("APPDATA", "cfg"), ("LOCALAPPDATA", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("HRDPS_UPDATE", raising=False)
    monkeypatch.setattr(update, "PUBLIC_KEY", PUB)
    return tmp_path


# ── Ed25519 ──────────────────────────────────────────────────────────────────
RFC = [("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
       ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")]


@pytest.mark.parametrize("seed,pub,msg,sig", RFC)
def test_rfc8032_vectors(seed, pub, msg, sig):
    seed, pub, msg, sig = map(bytes.fromhex, (seed, pub, msg, sig))
    assert ed25519.public_key(seed) == pub
    assert ed25519.sign(seed, msg) == sig
    assert ed25519.verify(pub, msg, sig)
    assert not ed25519.verify(pub, msg + b"!", sig)


def test_verify_rejects_garbage():
    sig = ed25519.sign(SEED, b"hello")
    assert ed25519.verify(PUB, b"hello", sig)
    assert not ed25519.verify(PUB, b"hello", sig[:-1] + bytes([sig[-1] ^ 1]))
    assert not ed25519.verify(PUB, b"hello", b"\x00" * 64)
    assert not ed25519.verify(PUB[:-1], b"hello", sig)
    assert not ed25519.verify(ed25519.public_key(b"\x01" * 32), b"hello", sig)


def test_matches_cryptography_when_available():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    k = ed.Ed25519PrivateKey.from_private_bytes(SEED)
    assert ed25519.sign(SEED, b"release") == k.sign(b"release")


# ── versions, modes ──────────────────────────────────────────────────────────
def test_parse_version():
    assert update.parse_version("v1.2.10") > update.parse_version("1.2.9") > update.parse_version("1.2")
    assert update.parse_version("1.10.0") > update.parse_version("1.9.9")


def test_mode_is_off_by_default_and_precedence(monkeypatch):
    assert update.mode() == "off"
    update.set_mode("notify")
    assert update.mode() == "notify"
    monkeypatch.setenv("HRDPS_UPDATE", "auto")
    assert update.mode() == "auto"                       # env beats the settings file
    with pytest.raises(ValueError):
        update.set_mode("sometimes")


# ── a fake release ───────────────────────────────────────────────────────────
def make_release(monkeypatch, asset, sums_name, payload=b"NEW-BINARY", tag="v9.9.9", sign_with=SEED, listed=True):
    sums = (f"{hashlib.sha256(payload).hexdigest()}  {asset}\n" if listed else "0" * 64 + "  other\n").encode()
    files = {asset: payload, sums_name: sums, sums_name + ".sig": ed25519.sign(sign_with, sums)}
    urls = {n: f"https://example.test/{n}" for n in files}
    monkeypatch.setattr(update.net, "get", lambda url, ua, timeout=30, tries=2: files.get(url.rsplit("/", 1)[-1]))
    return update.Update(version=tag.lstrip("v"), tag=tag, url="https://example.test/rel", assets=urls)


def linux_setup(monkeypatch, tmp_path):
    exe = tmp_path / "bin" / "hrdps-weather"
    exe.parent.mkdir()
    exe.write_bytes(b"OLD-BINARY")
    exe.chmod(0o755)
    monkeypatch.setattr(update, "install_kind", lambda: "linux-binary")
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    return exe


def test_download_verified_ok(monkeypatch):
    up = make_release(monkeypatch, "hrdps-weather-linux-x86_64", "SHA256SUMS-linux.txt")
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    assert update.download_verified(up, "linux-binary") == b"NEW-BINARY"


@pytest.mark.parametrize("problem", ["bad-signature", "bad-hash", "not-listed"])
def test_download_verified_refuses_tampering(monkeypatch, problem):
    kw = {"bad-signature": dict(sign_with=b"\x07" * 32), "bad-hash": {}, "not-listed": dict(listed=False)}[problem]
    up = make_release(monkeypatch, "hrdps-weather-linux-x86_64", "SHA256SUMS-linux.txt", **kw)
    if problem == "bad-hash":                               # sums are signed but the asset was swapped afterwards
        real = update.net.get
        monkeypatch.setattr(update.net, "get", lambda url, ua, timeout=30, tries=2:
                            b"EVIL" if url.endswith("hrdps-weather-linux-x86_64") else real(url, ua))
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    with pytest.raises(update.UpdateError):
        update.download_verified(up, "linux-binary")


def test_linux_binary_is_replaced_and_old_one_kept(monkeypatch, tmp_path):
    exe = linux_setup(monkeypatch, tmp_path)
    up = make_release(monkeypatch, "hrdps-weather-linux-x86_64", "SHA256SUMS-linux.txt")
    msg, must_exit = update.apply(up)
    assert exe.read_bytes() == b"NEW-BINARY" and os.access(exe, os.X_OK)
    assert (exe.parent / "hrdps-weather.old").read_bytes() == b"OLD-BINARY"
    assert not must_exit and "9.9.9" in msg


def test_linux_binary_untouched_when_verification_fails(monkeypatch, tmp_path):
    exe = linux_setup(monkeypatch, tmp_path)
    up = make_release(monkeypatch, "hrdps-weather-linux-x86_64", "SHA256SUMS-linux.txt", sign_with=b"\x09" * 32)
    with pytest.raises(update.UpdateError):
        update.apply(up)
    assert exe.read_bytes() == b"OLD-BINARY" and not list(exe.parent.glob("*.new"))


def test_windows_installer_is_verified_then_launched_silently(monkeypatch):
    monkeypatch.setattr(update, "install_kind", lambda: "windows-installed")
    up = make_release(monkeypatch, "hrdps-weather-setup-x86_64.exe", "SHA256SUMS.txt", payload=b"MZ-installer")
    launched = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw)))
    monkeypatch.setattr(update.subprocess, "DEVNULL", -3, raising=False)
    msg, must_exit = update.apply(up)
    cmd, _ = launched[0]
    assert cmd[1] == "/S" and open(cmd[0], "rb").read() == b"MZ-installer"
    assert must_exit                                      # the running program has to quit for the installer


def test_package_and_portable_installs_are_notify_only(monkeypatch):
    for kind in ("package", "windows-portable"):
        assert not update.can_self_update(kind)
        with pytest.raises(update.UpdateError):
            update.apply(update.Update("9.9.9", "v9.9.9", "u"), kind)


# ── checking ─────────────────────────────────────────────────────────────────
def test_check_finds_newer_and_throttles_to_once_a_day(monkeypatch):
    calls = []

    def fake_latest():
        calls.append(1)
        return update.Update("99.0.0", "v99.0.0", "https://example.test/rel", {})
    monkeypatch.setattr(update, "latest_release", fake_latest)
    assert update.check().version == "99.0.0"
    assert update.check().version == "99.0.0"             # served from the saved answer
    assert len(calls) == 1
    assert update.check(force=True) and len(calls) == 2


def test_check_same_or_older_version_is_none(monkeypatch):
    monkeypatch.setattr(update, "latest_release", lambda: update.Update(update.__version__, "v", "u", {}))
    assert update.check(force=True) is None


def test_network_failure_keeps_last_answer(monkeypatch):
    monkeypatch.setattr(update, "latest_release", lambda: update.Update("99.0.0", "v99.0.0", "u", {}))
    update.check(force=True)
    update._save_state(checked=0)                          # expired
    monkeypatch.setattr(update, "latest_release", lambda: None)
    assert update.check().version == "99.0.0"


def test_installed_notice_shown_once(monkeypatch):
    update._save_state(pending_version=update.__version__)
    assert update.__version__ in update.consume_installed_notice()
    assert update.consume_installed_notice() is None


# ── Updater (what the UI shows) ──────────────────────────────────────────────
def test_updater_notify_mode_sets_a_banner(monkeypatch):
    monkeypatch.setenv("HRDPS_UPDATE", "notify")
    monkeypatch.setattr(update, "check", lambda force=False: update.Update("99.0.0", "v99.0.0", "u", {}))
    monkeypatch.setattr(update, "can_self_update", lambda kind=None: True)
    up = update.Updater()
    up.refresh()
    assert "99.0.0" in up.message and "touche U" in up.message


def test_updater_package_install_gets_instructions_not_install(monkeypatch):
    monkeypatch.setattr(update, "check", lambda force=False: update.Update("99.0.0", "v99.0.0", "u", {}))
    monkeypatch.setattr(update, "install_kind", lambda: "package")
    up = update.Updater()
    up.refresh()
    assert "pip install -U" in up.message
    up.install()
    assert not up.busy


def test_updater_auto_mode_installs(monkeypatch):
    monkeypatch.setenv("HRDPS_UPDATE", "auto")
    monkeypatch.setattr(update, "check", lambda force=False: update.Update("99.0.0", "v99.0.0", "u", {}))
    monkeypatch.setattr(update, "can_self_update", lambda kind=None: True)
    done = []
    monkeypatch.setattr(update, "apply", lambda info, kind=None: (done.append(info.version) or ("installé", False)))
    up = update.Updater()
    up.refresh()
    for _ in range(50):
        if done and not up.busy:
            break
        time.sleep(0.05)
    assert done == ["99.0.0"] and up.message == "installé"


def test_off_by_default_means_no_network(monkeypatch, capsys):
    from hrdps_weather import cli

    def boom(*a, **k):
        raise AssertionError("the network must not be touched while updates are off")
    monkeypatch.setattr(update.net, "get", boom)
    assert cli.run_update(SimpleNamespace(mode=None, check=False, install=False)) == 0
    assert "désactivées" in capsys.readouterr().out
    up = update.Updater()
    up.start()                                             # mode off: starts nothing
    assert up._thread is None


def test_banner_is_drawn_and_clickable(data):
    from cairo import ImageSurface, Context, FORMAT_ARGB32
    from hrdps_weather import view
    v = view.View(data)
    v.updater = SimpleNamespace(message="⬆ Version 99.0.0 disponible — touche U ou clic pour installer")
    v.draw(Context(ImageSurface(FORMAT_ARGB32, view.W, view.H)), view.W, view.H)
    tags = [t for _, t, _ in v.hits]
    assert "update" in tags and getattr(v, "errors", 0) == 0
