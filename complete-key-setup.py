"""Local, hidden PIN entry and PIN-authorized hardware signature verification."""
from datetime import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys

from fido2.ctap import CtapError
from fido2.ctap2 import Ctap2
from fido2.ctap2.pin import ClientPin
from fido2.hid import CtapHidDevice

from hardware_check_support import confirmed_operation
from project_metadata import load_build_metadata, store_build_metadata
from pico_key_manager.identity import is_pico_descriptor

ROOT = Path(__file__).resolve().parent
RP_ID = "yunbikey-test.invalid"
REPORT = ROOT / "logs/complete-key-setup.json"
REPORT.parent.mkdir(parents=True, exist_ok=True)
result = {"completed": False, "pin_configured": False, "pin_verified": False}


def status(stage):
    result["stage"] = stage
    result["updated_at"] = datetime.now().astimezone().isoformat()
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, REPORT)


def main():
    if not sys.stdin.isatty():
        raise SystemExit("请在本地终端运行，PIN 不应发送到聊天窗口。")
    devices = []
    for candidate in CtapHidDevice.list_devices():
        if is_pico_descriptor(candidate.descriptor):
            devices.append(candidate)
        else:
            candidate.close()
    if len(devices) != 1:
        for candidate in devices:
            candidate.close()
        raise RuntimeError("请只连接一把 Pico FIDO 安全密钥。")

    device = devices[0]
    pin = None
    try:
        ctap = Ctap2(device)
        info = ctap.get_info()
        client = ClientPin(ctap)
        print("Pico 安全密钥：PIN 设置与签名测试\n")
        print("PIN 输入不会显示，也不会保存到日志或发送到聊天。")
        print("随后有两次按键测试：每次先按 Enter 开始，再短按板上 BOOTSEL。")
        print("这是本地临时凭据测试，不连接网站或使用你的 OpenAI 账号。\n")

        if info.options.get("clientPin"):
            status("waiting_for_existing_pin")
            pin = getpass.getpass("请输入这把钥匙现有的 PIN：")
            result["pin_configured"] = True
        else:
            status("waiting_for_new_pin")
            minimum = max(8, info.min_pin_length or 4)
            while True:
                pin = getpass.getpass(f"设置新 PIN（至少 {minimum} 个字符）：")
                if len(pin) < minimum or len(pin.encode("utf-8")) > 63:
                    print(f"请使用至少 {minimum} 个字符，UTF-8 编码不超过 63 字节。")
                    continue
                confirmation = getpass.getpass("再次输入新 PIN：")
                if confirmation != pin:
                    print("两次输入不同，请重新输入。设备尚未更改。")
                    continue
                confirmation = None
                client.set_pin(pin)
                result["pin_configured"] = bool(ctap.get_info().options.get("clientPin"))
                if not result["pin_configured"]:
                    raise RuntimeError("设备没有确认 PIN 已设置。")
                print("PIN 已设置。\n")
                break

        token = client.get_pin_token(
            pin, permissions=ClientPin.PERMISSION.MAKE_CREDENTIAL,
            permissions_rpid=RP_ID,
        )
        result["pin_verified"] = True
        print("PIN 验证通过。\n")
        registration_hash = os.urandom(32)
        authorization = client.protocol.authenticate(token, registration_hash)
        status("waiting_for_registration_button")
        registration = confirmed_operation(
            "第一次：准备好后按 Enter，然后短按一次 BOOTSEL 并松开。",
            lambda event, callback: ctap.make_credential(
                registration_hash,
                {"id": RP_ID, "name": "Disposable local test"},
                {"id": os.urandom(16), "name": "disposable-test"},
                [{"type": "public-key", "alg": -7}],
                options={"rk": False},
                pin_uv_param=authorization,
                pin_uv_protocol=client.protocol.VERSION,
                event=event, on_keepalive=callback,
            ),
        )
        credential = registration.auth_data.credential_data
        if credential is None or int(registration.auth_data.flags) & 5 != 5:
            raise RuntimeError("创建凭据没有确认用户在场和 PIN 验证。")
        if registration.auth_data.rp_id_hash != hashlib.sha256(RP_ID.encode()).digest():
            raise RuntimeError("创建凭据的站点绑定验证失败。")
        result["pin_protected_registration"] = True
        print("第一次按键确认通过，临时凭据创建成功。\n")

        token = client.get_pin_token(
            pin, permissions=ClientPin.PERMISSION.GET_ASSERTION,
            permissions_rpid=RP_ID,
        )
        client_hash = os.urandom(32)
        authorization = client.protocol.authenticate(token, client_hash)
        status("waiting_for_authentication_button")
        assertion = confirmed_operation(
            "第二次：准备好后按 Enter，然后再短按一次 BOOTSEL 并松开。",
            lambda event, callback: ctap.get_assertion(
                RP_ID, client_hash,
                allow_list=[{"type": "public-key", "id": credential.credential_id}],
                options={"up": True},
                pin_uv_param=authorization,
                pin_uv_protocol=client.protocol.VERSION,
                event=event, on_keepalive=callback,
            ),
        )
        if int(assertion.auth_data.flags) & 5 != 5:
            raise RuntimeError("登录签名没有确认用户在场和 PIN 验证。")
        if assertion.auth_data.rp_id_hash != hashlib.sha256(RP_ID.encode()).digest():
            raise RuntimeError("登录签名的站点绑定验证失败。")
        credential.public_key.verify(bytes(assertion.auth_data) + client_hash, assertion.signature)
        result["pin_protected_authentication"] = True
        result["touch_authentication_signature_verified"] = True
        result["completed"] = True
        status("completed")

        metadata = load_build_metadata()
        metadata["pin_configured"] = True
        metadata["pin_verified"] = True
        metadata["tested"].update(
            hardware=True, touch_registration=True,
            pin_protected_registration=True, pin_protected_authentication=True,
            touch_authentication_signature_verified=True,
        )
        metadata["tested"]["touch_required"] = bool(
            metadata["tested"].get("no_touch_registration_rejected")
            and metadata["tested"].get("no_touch_authentication_rejected")
        )
        store_build_metadata(metadata)
        print("\n全部通过：PIN 设置、PIN 验证、按键确认、签名验证。")
        print("可以返回聊天窗口。OpenAI / Daybreak 兼容性仍需账号页面验证。")
    finally:
        pin = None
        device.close()


if __name__ == "__main__":
    try:
        main()
    except CtapError as error:
        result["error"] = f"CTAP {int(error.code):#x}"
        status("failed")
        print("设备操作未完成：", str(error))
        print("如果 PIN 验证失败，请核对 PIN，不要连续猜测。")
    except (Exception, KeyboardInterrupt) as error:
        result["error"] = type(error).__name__
        status("failed")
        print("设置或测试未完成：", str(error))
    input("\n按 Enter 关闭窗口。")
