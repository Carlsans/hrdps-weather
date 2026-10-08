#!/usr/bin/env python3
"""Sign release checksum files with the project's Ed25519 key (used by the release workflows).

    UPDATE_SIGNING_SEED=<64 hex chars> python packaging/sign.py dist/SHA256SUMS.txt [more files...]

Writes `<file>.sig` (64 raw bytes) next to each file. The program verifies these signatures with the
public key embedded in hrdps_weather/update.py before it installs anything. Without a key in the
environment (forks, manual runs) nothing is signed and the script says so — such a release can be
downloaded by hand but the auto-updater will refuse it.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from hrdps_weather import ed25519, update  # noqa: E402

seed_hex = os.environ.get("UPDATE_SIGNING_SEED", "").strip()
if not seed_hex:
    print("UPDATE_SIGNING_SEED is not set: releasing WITHOUT a signature")
    sys.exit(0)
seed = bytes.fromhex(seed_hex)
if ed25519.public_key(seed) != update.PUBLIC_KEY:
    sys.exit("the signing key does not match the public key embedded in update.py — refusing to sign")
for name in sys.argv[1:]:
    data = Path(name).read_bytes()
    sig = ed25519.sign(seed, data)
    assert ed25519.verify(update.PUBLIC_KEY, data, sig)
    Path(name + ".sig").write_bytes(sig)
    print(f"signed {name} -> {name}.sig")
