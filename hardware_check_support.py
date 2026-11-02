"""Local user-paced CTAP operations, with bounded waiting and explicit retry."""
import threading

from fido2.ctap import CtapError


def confirmed_operation(prompt, callback):
    while True:
        input(prompt + "\n")
        event = threading.Event()
        timer = threading.Timer(40, event.set)
        prompted = False

        def keepalive(status):
            nonlocal prompted
            if int(status) == 2 and not prompted:
                prompted = True
                print("正在等待按键：请在 30 秒内短按 BOOTSEL，再松开。", flush=True)

        timer.start()
        try:
            return callback(event, keepalive)
        except CtapError as error:
            if int(error.code) not in (0x27, 0x2D, 0x2F):
                raise
            print("本轮按键确认已超时；准备好后可按 Enter 重试。", flush=True)
        finally:
            timer.cancel()
