"""Flash the prepared experimental UF2 to an already mounted RP2350 BOOTSEL disk."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
from project_metadata import load_build_metadata, store_build_metadata

ROOT = Path(__file__).resolve().parent
firmware = ROOT / "firmware/pico_fido_pico2_w_EXPERIMENTAL.uf2"
mounts = json.loads(subprocess.check_output(["findmnt", "-J", "-t", "vfat", "-o", "TARGET,LABEL"], text=True))
targets = [Path(item["target"]) for item in mounts.get("filesystems", []) if item.get("label") == "RP2350"]
if len(targets) != 1:
    raise SystemExit("Connect exactly one RP2350 board in BOOTSEL mode")
target = targets[0]
data = firmware.read_bytes()
if not data or len(data) % 512:
    raise SystemExit("Invalid UF2 length")
payload = []
for offset in range(0, len(data), 512):
    header = struct.unpack_from("<8I", data, offset)
    if header[:2] != (0x0A324655, 0x9E5D5157) or struct.unpack_from("<I", data, offset + 508)[0] != 0x0AB16F30:
        raise SystemExit("Invalid UF2 magic")
    if header[7] == 0xE48BFF59:
        payload.append(header)
if not payload or len(payload) != payload[0][6]:
    raise SystemExit("Incomplete RP2350 Arm Secure image")
if {header[5] for header in payload} != set(range(len(payload))):
    raise SystemExit("Invalid payload block sequence")
if not all(header[6] == len(payload) and 0x10000000 <= header[3] < 0x10400000 for header in payload):
    raise SystemExit("Image does not fit the Pico 2 W flash")
if not os.path.ismount(target) or "Board-ID: RP2350" not in (target / "INFO_UF2.TXT").read_text():
    raise SystemExit("RP2350 BOOTSEL disk is not mounted")
mount_info = subprocess.check_output(
    ["findmnt", "--noheadings", "--output", "FSTYPE,LABEL", "--mountpoint", str(target)], text=True
).split()
if mount_info != ["vfat", "RP2350"]:
    raise SystemExit("Unexpected target filesystem")

checksum = hashlib.sha256(data).hexdigest()
metadata = load_build_metadata()
metadata["source_patches"] = [
    "patches/pico2w-led-driver.patch",
    "patches/pico2w-touch-default.patch",
    "patches/pico2w-user-presence.patch",
]
metadata["tested"].update(
    hardware=False, touch_required=False, no_touch_registration_rejected=False,
    no_touch_authentication_rejected=False, touch_authentication_signature_verified=False,
)
metadata["uf2"].update(
    sha256=checksum, bytes=len(data), total_blocks=len(data) // 512,
    arm_secure_blocks=len(payload),
)
(ROOT / "firmware/SHA256SUMS").write_text(f"{checksum}  {firmware.name}\n")
(target / "pico_fido.uf2").write_bytes(data)
os.sync()
metadata["hardware_flashed"] = True
metadata["hardware_flash_time"] = datetime.now().astimezone().isoformat()
store_build_metadata(metadata)
print("FIRMWARE_TRANSFERRED", checksum, flush=True)
for _ in range(20):
    for device in Path("/sys/bus/usb/devices").glob("*"):
        try:
            if (device / "idVendor").read_text().strip() == "2e8a" and (device / "idProduct").read_text().strip() == "10fe":
                print("PICO_FIDO_REENUMERATED", flush=True)
                raise SystemExit(0)
        except OSError:
            pass
    time.sleep(1)
raise SystemExit("Transferred, but Pico FIDO was not detected within 20 seconds")
