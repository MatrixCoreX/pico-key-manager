"""Local FIDO2 operations. PINs and credentials are never written to logs."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from fido2.ctap import CtapError
from fido2.ctap2 import Ctap2
from fido2.ctap2.credman import CredentialManagement
from fido2.ctap2.pin import ClientPin
from fido2.hid import CtapHidDevice, list_descriptors, open_connection
from .identity import is_pico_descriptor, usb_mode
from .uf2 import inspect_uf2

RP_ID = "pico-key-manager-test.invalid"


class DeviceError(Exception):
    pass


@dataclass(frozen=True)
class DeviceRef:
    path: str
    name: str
    serial: str
    vid: int
    pid: int

    @property
    def fingerprint(self):
        return hashlib.sha256(f"{self.vid}:{self.pid}:{self.serial or self.path}".encode()).hexdigest()


def discover():
    result = []
    for item in list_descriptors():
        if is_pico_descriptor(item):
            result.append(DeviceRef(str(item.path), item.product_name or "Pico FIDO", item.serial_number or "", item.vid, item.pid))
    return result


def friendly_error(error):
    if isinstance(error, DeviceError):
        return str(error)
    if isinstance(error, CtapError):
        messages = {
            0x27: "按键确认超时或操作被拒绝。请在提示后短按 BOOTSEL 并松开。",
            0x2D: "操作已取消。", 0x2F: "等待按键超时，请重新开始。",
            0x30: "设备状态不允许此操作。重置需在重新插入后的几秒内开始。",
            0x31: "PIN 不正确。请核对后重试，不要连续猜测。",
            0x32: "PIN 已被锁定，需要重置 FIDO 功能后才能重新设置。",
            0x33: "PIN 授权无效或已过期，请重新输入 PIN。",
            0x34: "PIN 暂时锁定，请拔下设备再插回后重试。",
            0x35: "请先设置设备 PIN。",
            0x36: "此操作需要 PIN 授权。", 0x37: "PIN 不符合设备要求。",
            0x2E: "设备中没有匹配的 Passkey。",
            0x2B: "当前固件不支持此选项。",
            0x26: "当前固件不支持所选签名算法，请尝试 ES256。",
            0x38: "PIN 授权已过期，请重新输入 PIN。",
            0x28: "设备中的凭据空间不足。",
        }
        return messages.get(int(error.code), f"设备返回错误：{error.code.name}（{int(error.code):#x}）。")
    if isinstance(error, PermissionError):
        return "没有访问 USB 安全密钥的权限。请重新插入设备，并确认当前桌面会话可访问它。"
    if isinstance(error, OSError):
        return "设备连接已中断或 USB 操作失败。请重新连接设备。"
    return "操作未完成，请刷新设备后重试。"


@contextmanager
def session(ref):
    current = next((x for x in discover() if x.path == ref.path and x.fingerprint == ref.fingerprint), None)
    if current is None:
        raise DeviceError("原设备已断开或发生变化，请重新选择设备。")
    descriptor = next((x for x in list_descriptors() if str(x.path) == ref.path), None)
    if descriptor is None:
        raise DeviceError("设备已断开。")
    device = None
    try:
        device = CtapHidDevice(descriptor, open_connection(descriptor))
        yield Ctap2(device)
    except (CtapError, OSError) as error:
        raise DeviceError(friendly_error(error)) from None
    finally:
        if device is not None:
            device.close()


def describe(ref):
    with session(ref) as ctap:
        info = ctap.get_info()
        firmware = info.firmware_version
        return {
            "name": ref.name, "path": ref.path, "usb_id": f"{ref.vid:04x}:{ref.pid:04x}",
            "usb_mode": usb_mode(ref.vid, ref.pid),
            "firmware": f"{firmware >> 8}.{firmware & 255}" if firmware is not None else "未知",
            "versions": list(info.versions), "pin_set": info.options.get("clientPin"),
            "resident_keys": bool(info.options.get("rk")),
            "credential_management": CredentialManagement.is_supported(info),
            "algorithms": [item.get("alg") for item in info.algorithms or []],
            "extensions": list(info.extensions), "aaguid": str(info.aaguid),
            "min_pin_length": info.min_pin_length or 4,
        }


def validate_pin(pin, confirmation, minimum=8):
    if len(pin) < minimum or len(pin.encode("utf-8")) > 63:
        raise DeviceError(f"新 PIN 至少需要 {minimum} 个字符，UTF-8 编码不得超过 63 字节。")
    if pin != confirmation:
        raise DeviceError("两次输入的新 PIN 不同。")


def set_pin(ref, pin):
    with session(ref) as ctap:
        if ctap.get_info().options.get("clientPin"):
            raise DeviceError("设备已设置 PIN，请使用“修改 PIN”。")
        validate_pin(pin, pin, max(8, ctap.get_info().min_pin_length or 4))
        ClientPin(ctap).set_pin(pin)
        if not ctap.get_info().options.get("clientPin"):
            raise DeviceError("设备没有确认 PIN 已设置。")


def change_pin(ref, old_pin, new_pin):
    with session(ref) as ctap:
        validate_pin(new_pin, new_pin, max(8, ctap.get_info().min_pin_length or 4))
        ClientPin(ctap).change_pin(old_pin, new_pin)


def verify_pin(ref, pin):
    with session(ref) as ctap:
        client = ClientPin(ctap)
        client.get_pin_token(pin, permissions=ClientPin.PERMISSION.MAKE_CREDENTIAL, permissions_rpid=RP_ID)
        return True


def _manager(ctap, pin):
    if not CredentialManagement.is_supported(ctap.info):
        raise DeviceError("当前固件不支持 Passkey 管理。")
    client = ClientPin(ctap)
    token = client.get_pin_token(pin, permissions=ClientPin.PERMISSION.CREDENTIAL_MGMT)
    return CredentialManagement(ctap, client.protocol, token)


def list_credentials(ref, pin):
    with session(ref) as ctap:
        manager = _manager(ctap, pin)
        metadata = manager.get_metadata()
        rows = []
        for rp in manager.enumerate_rps():
            rp_data = rp[CredentialManagement.RESULT.RP]
            for credential in manager.enumerate_creds(rp[CredentialManagement.RESULT.RP_ID_HASH]):
                user = credential.get(CredentialManagement.RESULT.USER, {})
                identifier = credential[CredentialManagement.RESULT.CREDENTIAL_ID]
                rows.append({
                    "site": rp_data.get("id", "未知网站"),
                    "site_name": rp_data.get("name", ""),
                    "user": user.get("displayName") or user.get("name") or "未命名账号",
                    "credential_id": bytes(identifier["id"]),
                })
        return {"credentials": rows, "count": metadata.get(1, len(rows)), "remaining": metadata.get(2)}


def delete_credential(ref, pin, credential_id):
    if not isinstance(credential_id, bytes) or not credential_id:
        raise DeviceError("无效的凭据。")
    with session(ref) as ctap:
        _manager(ctap, pin).delete_cred({"type": "public-key", "id": credential_id})


def _timed(callback, event, progress, message):
    timer = threading.Timer(40, event.set)
    prompted = False

    def keepalive(status):
        nonlocal prompted
        if int(status) == 2 and not prompted:
            prompted = True
            progress(message)

    timer.start()
    try:
        if event.is_set():
            raise DeviceError("操作已取消。")
        return callback(keepalive)
    finally:
        timer.cancel()


def signature_test(ref, pin, event, progress, algorithm=-7):
    with session(ref) as ctap:
        client = ClientPin(ctap)
        token = client.get_pin_token(pin, permissions=ClientPin.PERMISSION.MAKE_CREDENTIAL, permissions_rpid=RP_ID)
        client_hash = os.urandom(32)
        registration = _timed(
            lambda callback: ctap.make_credential(
                client_hash, {"id": RP_ID, "name": "Pico Key Manager local test"},
                {"id": os.urandom(16), "name": "temporary-test"},
                [{"type": "public-key", "alg": algorithm}], options={"rk": False},
                pin_uv_param=client.protocol.authenticate(token, client_hash),
                pin_uv_protocol=client.protocol.VERSION, event=event, on_keepalive=callback,
            ), event, progress, "第 1 / 2 次：请在 30 秒内短按 BOOTSEL，创建临时凭据。",
        )
        credential = registration.auth_data.credential_data
        expected_rp = hashlib.sha256(RP_ID.encode()).digest()
        if credential is None or registration.auth_data.rp_id_hash != expected_rp or int(registration.auth_data.flags) & 5 != 5:
            raise DeviceError("注册数据未通过 PIN、用户在场或站点绑定验证。")
        progress("凭据创建成功。正在准备第二次按键确认…")
        token = client.get_pin_token(pin, permissions=ClientPin.PERMISSION.GET_ASSERTION, permissions_rpid=RP_ID)
        client_hash = os.urandom(32)
        assertion = _timed(
            lambda callback: ctap.get_assertion(
                RP_ID, client_hash, allow_list=[{"type": "public-key", "id": credential.credential_id}],
                options={"up": True}, pin_uv_param=client.protocol.authenticate(token, client_hash),
                pin_uv_protocol=client.protocol.VERSION, event=event, on_keepalive=callback,
            ), event, progress, "第 2 / 2 次：请再短按一次 BOOTSEL，完成登录签名。",
        )
        if assertion.auth_data.rp_id_hash != expected_rp or int(assertion.auth_data.flags) & 5 != 5:
            raise DeviceError("签名未通过 PIN、用户在场或站点绑定验证。")
        try:
            credential.public_key.verify(bytes(assertion.auth_data) + client_hash, assertion.signature)
        except Exception:
            raise DeviceError("设备返回的签名验证失败。") from None
        return {"passed": True, "algorithm": "ES256" if algorithm == -7 else "EdDSA", "time": datetime.now().astimezone().isoformat()}


def reset_when_reconnected(ref, event, progress):
    progress("请拔下设备，再正常插回（不要按住 BOOTSEL）。等待重新连接…")
    disappeared = False
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if event.wait(0.25):
            raise DeviceError("重置已取消，未清空设备。")
        candidates = [item for item in discover() if (item.vid, item.pid) == (ref.vid, ref.pid) and (not ref.serial or item.serial == ref.serial)]
        if not candidates:
            disappeared = True
        elif disappeared:
            if len(candidates) != 1:
                raise DeviceError("发现多把设备，请只连接需要重置的一把。")
            with session(candidates[0]) as ctap:
                _timed(lambda callback: ctap.reset(event=event, on_keepalive=callback), event, progress, "请短按 BOOTSEL 确认清空 FIDO 凭据。")
            return
    raise DeviceError("未在 60 秒内检测到拔插，设备没有重置。")


def boot_volume():
    found = []
    try:
        raw = subprocess.check_output(["findmnt", "-J", "-t", "vfat", "-o", "TARGET,LABEL"], text=True)
        for item in json.loads(raw).get("filesystems", []):
            target = Path(item["target"])
            if item.get("label") == "RP2350" and "Board-ID: RP2350" in (target / "INFO_UF2.TXT").read_text():
                found.append(target)
    except (subprocess.CalledProcessError, OSError, ValueError):
        pass
    return found[0] if len(found) == 1 else None


def validate_uf2(path):
    try:
        return {"path": str(Path(path).resolve()), **inspect_uf2(Path(path).read_bytes())}
    except ValueError as error:
        raise DeviceError(str(error)) from None


def flash_uf2(path, expected_sha256, progress):
    details = validate_uf2(path)
    if details["sha256"] != expected_sha256:
        raise DeviceError("选定文件已变化，请重新选择固件。")
    target = boot_volume()
    if target is None:
        raise DeviceError("没有找到 RP2350 烧录盘。请按住 BOOTSEL 插入设备。")
    progress("正在写入固件，请保持 USB 连接…")
    (target / "pico_key.uf2").write_bytes(Path(path).read_bytes())
    os.sync()
    return details
