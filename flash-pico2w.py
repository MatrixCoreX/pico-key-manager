"""Flash a checked profile to one mounted RP2350 BOOTSEL disk."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
from pico_key_manager.identity import PROFILES
from pico_key_manager.uf2 import inspect_uf2
from project_metadata import store_build_metadata

ROOT = Path(__file__).resolve().parent

def prepared_image(profile):
    config = PROFILES[profile]
    path = ROOT / "firmware" / config["file"]
    data = path.read_bytes()
    details = inspect_uf2(data)
    metadata = json.loads((ROOT / "firmware" / config["manifest"]).read_text())
    if details["sha256"] != metadata["uf2"]["sha256"]:
        raise ValueError("Firmware checksum differs from the build manifest")
    if metadata["usb_identity"] != config["usb_identity"]:
        raise ValueError("Unexpected USB identity in the build manifest")
    metadata.update(profile=profile, uf2={"file": "firmware/" + config["file"], **details})
    metadata["tested"] = {"compiled": True, "hardware": False, "touch_required": False,
                          "openai_registration": False, "daybreak_eligibility": False}
    return data, metadata


def boot_target():
    try:
        raw = subprocess.check_output(["findmnt", "-J", "-t", "vfat", "-o", "TARGET,LABEL"], text=True)
        mounts = json.loads(raw).get("filesystems", [])
    except subprocess.CalledProcessError:
        mounts = []
    targets = [Path(item["target"]) for item in mounts if item.get("label") == "RP2350"]
    if len(targets) != 1:
        raise ValueError("Connect exactly one RP2350 board in BOOTSEL mode")
    target = targets[0]
    if not os.path.ismount(target) or "Board-ID: RP2350" not in (target / "INFO_UF2.TXT").read_text():
        raise ValueError("RP2350 BOOTSEL disk is not mounted")
    mount_info = subprocess.check_output(
        ["findmnt", "--noheadings", "--output", "FSTYPE,LABEL", "--mountpoint", str(target)], text=True,
    ).split()
    if mount_info != ["vfat", "RP2350"]:
        raise ValueError("Unexpected target filesystem")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=PROFILES, nargs="?", default="native")
    parser.add_argument("--check-only", action="store_true", help="Validate the image without accessing hardware")
    args = parser.parse_args()
    data, metadata = prepared_image(args.profile)
    print(json.dumps({"profile": args.profile, "usb_identity": metadata["usb_identity"],
                      "uf2": metadata["uf2"]}, ensure_ascii=False), flush=True)
    if args.check_only:
        return
    target = boot_target()
    (target / "pico_fido.uf2").write_bytes(data)
    os.sync()
    metadata["hardware_flashed"] = True
    metadata["hardware_flash_time"] = datetime.now().astimezone().isoformat()
    store_build_metadata(metadata)
    print("FIRMWARE_TRANSFERRED", metadata["uf2"]["sha256"], flush=True)
    vendor, product = metadata["usb_identity"].split(":")
    for _ in range(20):
        for device in Path("/sys/bus/usb/devices").glob("*"):
            try:
                if ((device / "idVendor").read_text().strip() == vendor
                        and (device / "idProduct").read_text().strip() == product
                        and "pico" in (device / "product").read_text().casefold()):
                    print("PICO_FIDO_REENUMERATED", metadata["usb_identity"], flush=True)
                    return
            except OSError:
                pass
        time.sleep(1)
    raise ValueError("Transferred, but the selected Pico profile was not detected within 20 seconds")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
