"""Record build provenance, without importing any local device state."""
import copy
import hashlib
import json
from pathlib import Path
import sys

from pico_key_manager.identity import PROFILES
from pico_key_manager.uf2 import inspect_uf2

ROOT = Path(__file__).resolve().parent


def record(profile):
    config = PROFILES[profile]
    firmware = ROOT / "firmware" / config["file"]
    data = firmware.read_bytes()
    details = inspect_uf2(data)
    manifest_path = ROOT / "firmware" / config["manifest"]
    base = json.loads((ROOT / "firmware/build-manifest.json").read_text())
    manifest = copy.deepcopy(base)
    manifest.pop("reference_validation", None)
    manifest.pop("reference_validation_note", None)
    manifest.update(profile=profile, usb_identity=config["usb_identity"])
    manifest["flags"]["VIDPID"] = config["vidpid"]
    manifest["uf2"] = {
        "file": "firmware/" + config["file"], **details,
        "structural_validation": "passed; payload confined to the 1 MiB firmware partition",
    }
    manifest["tested"] = {"compiled": False, "hardware": False, "touch_required": False, "openai_registration": False, "daybreak_eligibility": False}
    manifest["reference_validation"] = {"compiled": True, "hardware": False, "yubico_attestation": False, "daybreak_eligibility": False}
    manifest["reference_validation_note"] = "Build validation only; hardware results must be recorded separately for this exact image."
    manifest["compatibility_note"] = (
        "USB VID/PID compatibility only; Pico product and WebAuthn attestation are retained."
        if profile == "yubikey-compat" else "Native PicoKeys USB identity and WebAuthn attestation."
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    checksums = []
    for item in PROFILES.values():
        path = ROOT / "firmware" / item["file"]
        if path.exists():
            checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (ROOT / "firmware/SHA256SUMS").write_text("\n".join(checksums) + "\n")
    print(json.dumps({"profile": profile, "usb_identity": manifest["usb_identity"], "uf2": manifest["uf2"]}, indent=2))


if __name__ == "__main__":
    record(sys.argv[1] if len(sys.argv) > 1 else "native")
