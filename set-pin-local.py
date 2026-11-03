"""Run in a local terminal. PIN input is hidden and is never saved or logged."""
import getpass
import json
from pathlib import Path
import sys

from fido2.ctap2 import Ctap2
from fido2.ctap2.pin import ClientPin
from fido2.hid import CtapHidDevice
from project_metadata import load_build_metadata, store_build_metadata
from pico_key_manager.identity import is_pico_descriptor

if not sys.stdin.isatty():
    raise SystemExit("请在本地终端运行；PIN 不应发送到聊天窗口。")

device = None
for candidate in CtapHidDevice.list_devices():
    if is_pico_descriptor(candidate.descriptor):
        device = candidate
        break
    candidate.close()
if device is None:
    raise SystemExit("没有找到可访问的 Pico FIDO。")

try:
    ctap = Ctap2(device)
    if ctap.get_info().options.get("clientPin"):
        raise SystemExit("这把钥匙已经设置过 PIN，本脚本不会更改它。")
    print("为 Pico 安全密钥设置 PIN。输入不会显示，也不会保存到文件或聊天。")
    pin = getpass.getpass("新 PIN（建议至少 8 位）：")
    if not 8 <= len(pin) or len(pin.encode("utf-8")) > 63:
        raise SystemExit("请使用至少 8 个字符，且 UTF-8 编码不超过 63 字节的 PIN。")
    confirm = getpass.getpass("再次输入 PIN：")
    if pin != confirm:
        raise SystemExit("两次输入不同，设备没有更改。")
    ClientPin(ctap).set_pin(pin)
    pin = confirm = None
    configured = bool(ctap.get_info().options.get("clientPin"))
    if not configured:
        raise SystemExit("设备没有确认 PIN 已设置。")
    metadata = load_build_metadata()
    metadata["pin_configured"] = True
    store_build_metadata(metadata)
    print("PIN 已设置。请自行保管；无需发给我。")
finally:
    device.close()
