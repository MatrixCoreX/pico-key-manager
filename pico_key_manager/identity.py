"""Identify Pico firmware without treating genuine YubiKeys as Pico devices."""
NATIVE_USB = (0x2E8A, 0x10FE)
YUBIKEY_COMPAT_USB = (0x1050, 0x0407)
OTHER_PICO_USB = {(0x1209, 0x0001)}

PROFILES = {
    "native": {
        "vidpid": "None", "usb_identity": "2e8a:10fe",
        "file": "pico_fido_pico2_w_EXPERIMENTAL.uf2",
        "manifest": "build-manifest.json",
    },
    "yubikey-compat": {
        "vidpid": "Yubikey5", "usb_identity": "1050:0407",
        "file": "pico_fido_pico2_w_YUBIKEY_USB_COMPAT_EXPERIMENTAL.uf2",
        "manifest": "build-manifest-yubikey-compat.json",
    },
    "usb-repair": {
        "vidpid": "None", "usb_identity": "2e8a:10fe",
        "file": "pico_fido_pico2_w_USB_REPAIR_EXPERIMENTAL.uf2",
        "manifest": "build-manifest-usb-repair.json",
    },
}


def is_pico_descriptor(descriptor):
    identifier = (descriptor.vid, descriptor.pid)
    if identifier == NATIVE_USB or identifier in OTHER_PICO_USB:
        return True
    name = (descriptor.product_name or "").casefold()
    return identifier == YUBIKEY_COMPAT_USB and "pico" in name


def usb_mode(vid, pid):
    if (vid, pid) == YUBIKEY_COMPAT_USB:
        return "YubiKey USB 兼容模式"
    return "Pico 原生身份" if (vid, pid) == NATIVE_USB else "FIDO USB 密钥"
