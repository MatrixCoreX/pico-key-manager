"""Validate RP2350 firmware without accepting writes to PicoKeys private data."""
import hashlib
import struct


def inspect_uf2(data):
    if not data or len(data) % 512 or len(data) > 16 * 1024 * 1024:
        raise ValueError("不是有效的 UF2 文件。")
    payload = []
    families = set()
    for offset in range(0, len(data), 512):
        header = struct.unpack_from("<8I", data, offset)
        if (header[:2] != (0x0A324655, 0x9E5D5157)
                or struct.unpack_from("<I", data, offset + 508)[0] != 0x0AB16F30
                or not 0 < header[4] <= 476 or not header[2] & 0x2000):
            raise ValueError("UF2 数据块校验失败。")
        families.add(hex(header[7]))
        if header[7] == 0xE48BFF59:
            payload.append(header)
        elif not (header[7] == 0xE48BFF57 and header[2] == 0xA000
                  and header[3] == 0x10FFFF00 and header[4] == 256):
            raise ValueError("只支持 RP2350 Arm Secure 固件及其绝对地址标记。")
    if not payload or len(payload) != payload[0][6] or {h[5] for h in payload} != set(range(len(payload))):
        raise ValueError("需要完整的 RP2350 Arm Secure UF2 固件。")
    if not all(h[6] == len(payload) and 0x10000000 <= h[3]
               and h[3] + h[4] <= 0x10100000 for h in payload):
        raise ValueError("固件超出 1 MiB 程序分区，可能覆盖 PIN 或凭据数据，已拒绝。")
    return {
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "total_blocks": len(data) // 512, "arm_secure_blocks": len(payload),
        "family_ids": sorted(families),
        "payload_start": hex(min(h[3] for h in payload)),
        "payload_end": hex(max(h[3] + h[4] for h in payload)),
    }
