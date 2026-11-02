"""Disposable hardware tests. No OpenAI account or PIN is used."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from fido2.ctap import CtapError
from fido2.ctap2 import Ctap2
from fido2.hid import CtapHidDevice

ROOT = Path(__file__).resolve().parent
RP_ID = "yunbikey-test.invalid"


def run_call(callback):
    event = threading.Event()
    timer = threading.Timer(40, event.set)
    statuses = set()

    def keepalive(status):
        code = int(status)
        if code not in statuses:
            statuses.add(code)
            print("KEEPALIVE", code, flush=True)

    timer.start()
    started = time.monotonic()
    try:
        response = callback(event, keepalive)
        return response, None, time.monotonic() - started, statuses
    except CtapError as exc:
        return None, int(exc.code), time.monotonic() - started, statuses
    finally:
        timer.cancel()


def create(ctap, event, callback):
    return ctap.make_credential(
        os.urandom(32),
        {"id": RP_ID, "name": "Disposable local test"},
        {"id": os.urandom(16), "name": "disposable-test"},
        [{"type": "public-key", "alg": -7}],
        options={"rk": False},
        event=event,
        on_keepalive=callback,
    )


def denied_after_wait(error, elapsed, statuses):
    return error in (0x27, 0x2F) and elapsed >= 28 and 2 in statuses


def confirmed_call(prompt, callback):
    while True:
        input(prompt + "\n")
        response, error, elapsed, statuses = run_call(callback)
        if response is not None:
            return response
        if error not in (0x27, 0x2D, 0x2F):
            raise RuntimeError(f"CTAP operation failed: {error!r}")
        print("TOUCH_CONFIRMATION_EXPIRED; waiting for explicit retry.", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("no-touch-registration", "interactive"))
    args = parser.parse_args()
    device = None
    for candidate in CtapHidDevice.list_devices():
        if candidate.descriptor.vid == 0x2E8A and candidate.descriptor.pid == 0x10FE:
            device = candidate
            break
        candidate.close()
    if device is None:
        raise SystemExit("Pico FIDO not accessible")
    ctap = Ctap2(device)
    if ctap.get_info().options.get("clientPin"):
        device.close()
        raise SystemExit("PIN is configured; use browser PIN-protected testing instead.")

    results = {}
    try:
        if args.mode == "no-touch-registration":
            response, error, elapsed, statuses = run_call(
                lambda event, callback: create(ctap, event, callback)
            )
            results.update(
                no_touch_registration_rejected=response is None
                and denied_after_wait(error, elapsed, statuses),
                error_code=hex(error) if error is not None else None,
                elapsed_seconds=round(elapsed, 2),
                keepalive_statuses=sorted(statuses),
            )
        else:
            registration = confirmed_call(
                "READY: press Enter to begin touch-confirmed registration.",
                lambda event, callback: create(ctap, event, callback)
            )
            credential = registration.auth_data.credential_data
            assert credential is not None
            assert registration.auth_data.rp_id_hash == hashlib.sha256(RP_ID.encode()).digest()
            assert int(registration.auth_data.flags) & 1
            results["touch_registration"] = True
            print("REGISTRATION_OK", flush=True)

            allow_list = [{"type": "public-key", "id": credential.credential_id}]
            input("READY: press Enter to begin the no-touch assertion test.\n")
            response, error, elapsed, statuses = run_call(
                lambda event, callback: ctap.get_assertion(
                    RP_ID, os.urandom(32), allow_list=allow_list,
                    options={"up": True}, event=event, on_keepalive=callback,
                )
            )
            results["no_touch_authentication_rejected"] = response is None and denied_after_wait(
                error, elapsed, statuses
            )
            print("NO_TOUCH_ASSERTION", json.dumps({
                "passed": results["no_touch_authentication_rejected"],
                "error_code": hex(error) if error is not None else None,
                "elapsed_seconds": round(elapsed, 2),
            }), flush=True)
            if not results["no_touch_authentication_rejected"]:
                raise RuntimeError("Authentication did not enforce physical presence")

            client_hash = os.urandom(32)
            assertion = confirmed_call(
                "READY: press Enter to begin touch-confirmed authentication.",
                lambda event, callback: ctap.get_assertion(
                    RP_ID, client_hash, allow_list=allow_list,
                    options={"up": True}, event=event, on_keepalive=callback,
                )
            )
            assert assertion.auth_data.rp_id_hash == hashlib.sha256(RP_ID.encode()).digest()
            assert int(assertion.auth_data.flags) & 1
            credential.public_key.verify(bytes(assertion.auth_data) + client_hash, assertion.signature)
            results["touch_authentication_signature_verified"] = True
    finally:
        device.close()
        output = ROOT / "logs" / f"hardware-{args.mode}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print("RESULT", json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
