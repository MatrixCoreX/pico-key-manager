"""Render this application's own widgets and verify non-destructive UI flows."""
import json
from pathlib import Path
import sys
import time
import threading
from unittest.mock import patch

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib, Gtk

from pico_key_manager.application import Application, ROOT
(ROOT / 'logs').mkdir(parents=True, exist_ok=True)


class Smoke(Application):
    def __init__(self):
        super().__init__()
        self.set_application_id('io.github.guagua.PicoKeyManager.Smoke')
        self.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        self.result = {'pages': [], 'screenshots': [], 'errors': []}
        self.started = time.monotonic()
        self.scheduled = False

    def do_activate(self):
        super().do_activate()
        self.window.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        GLib.timeout_add(150, self.wait_ready)

    def wait_ready(self):
        if self.window.busy and time.monotonic() - self.started < 12:
            return True
        try:
            if self.window.info is None:
                raise RuntimeError('Live device information was not loaded')
            self.result['live_device'] = self.window.info['usb_id']
            self.result['pin_set'] = self.window.info['pin_set']
            self.index = 0
            self.next_page()
        except Exception as error:
            self.fail(error)
        return False

    def capture(self, name, target=None):
        window = target or self.window
        paintable = Gtk.WidgetPaintable.new(window)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
        if node is None:
            raise RuntimeError('Empty widget snapshot')
        texture = window.get_renderer().render_texture(node, None)
        path = ROOT / 'logs' / f'ui-{name}.png'
        texture.save_to_png(str(path))
        self.result['screenshots'].append(str(path))

    def next_page(self):
        if self.index < len(self.window.page_ids):
            name = self.window.page_ids[self.index]
            self.window.go(name)
            GLib.timeout_add(200, self.capture_page, name)
        else:
            self.dialog_test()
        return False

    def capture_page(self, name):
        try:
            if self.window.stack.get_visible_child_name() != name:
                raise RuntimeError('Navigation did not select ' + name)
            self.capture(name)
            if name == 'credentials':
                self.window._show_credentials({'credentials': [{'site': 'example.invalid', 'user': 'Synthetic test account', 'credential_id': b'ui-only-test-id'}], 'count': 1, 'remaining': 99})
                GLib.timeout_add(250, self.capture_populated)
                return False
            self.result['pages'].append(name)
            self.index += 1
            self.next_page()
        except Exception as error:
            self.fail(error)
        return False

    def capture_populated(self):
        try:
            self.capture('credentials-populated')
            self.result['pages'].append('credentials')
            self.index += 1
            self.next_page()
        except Exception as error:
            self.fail(error)
        return False

    def dialog_test(self):
        self.window.go('pin')
        self.submitted = False
        self.window._pin_dialog('修改 PIN · 界面测试', lambda _: setattr(self, 'submitted', True), new=True)
        GLib.timeout_add(200, self.check_dialog)

    def check_dialog(self):
        try:
            dialog = self.window.get_visible_dialog()
            if dialog is None or dialog.get_response_enabled('apply'):
                raise RuntimeError('Empty PIN form should be disabled')
            fields = dialog.get_extra_child()
            entries = []
            def collect(widget):
                if isinstance(widget, Gtk.PasswordEntry):
                    entries.append(widget)
                    return
                child = widget.get_first_child()
                while child:
                    collect(child)
                    child = child.get_next_sibling()
            collect(fields)
            if len(entries) != 3:
                raise RuntimeError('Expected current/new/confirmation fields')
            entries[0].set_text('synthetic-current-pin')
            entries[1].set_text('synthetic-new-pin')
            entries[2].set_text('different-test-pin')
            if dialog.get_response_enabled('apply'):
                raise RuntimeError('Mismatched PIN confirmation was accepted')
            entries[2].set_text('synthetic-new-pin')
            if not dialog.get_response_enabled('apply'):
                raise RuntimeError('Valid PIN form stayed disabled')
            GLib.timeout_add(300, self.capture_dialog, dialog)
        except Exception as error:
            self.fail(error)
        return False

    def capture_dialog(self, dialog):
        try:
            self.capture('pin-dialog', dialog.get_root())
            dialog.close()
            GLib.timeout_add(200, self.finish)
        except Exception as error:
            self.fail(error)
        return False

    def finish(self):
        if self.submitted:
            self.fail(RuntimeError('Cancelling a PIN form invoked its operation'))
        else:
            self.result['pin_form_validation'] = True
            self.result['cancel_does_not_submit'] = True
            self.reset_dialog_test()
        return False

    def reset_dialog_test(self):
        self.window._confirm_reset()
        GLib.timeout_add(200, self.check_reset_dialog)

    def check_reset_dialog(self):
        try:
            dialog = self.window.get_visible_dialog()
            if dialog.get_response_enabled('reset'):
                raise RuntimeError('Destructive reset was enabled without confirmation')
            entry = dialog.get_extra_child()
            entry.set_text('清')
            if dialog.get_response_enabled('reset'):
                raise RuntimeError('Partial reset confirmation was accepted')
            entry.set_text('清空')
            if not dialog.get_response_enabled('reset'):
                raise RuntimeError('Explicit reset confirmation was not accepted')
            dialog.close()
            self.result['reset_requires_explicit_confirmation'] = True
            GLib.timeout_add(200, self.offline_test)
        except Exception as error:
            self.fail(error)
        return False

    def offline_test(self):
        try:
            with patch('pico_key_manager.application.hardware.boot_volume', return_value=None):
                self.window.refresh([])
            if self.window.ref is not None or any(widget.get_sensitive() for widget in self.window.operation_buttons):
                raise RuntimeError('Device actions stayed enabled after disconnection')
            if self.window.credential_count.get_text() != '尚未解锁':
                raise RuntimeError('Credential list was retained after disconnection')
            self.result['offline_disables_device_actions'] = True
            self.result['disconnect_clears_credential_view'] = True
            old_event = self.window.cancel_event
            def operation(event, progress):
                event.wait(3)
                if event.is_set():
                    from pico_key_manager.device import DeviceError
                    raise DeviceError('Operation cancelled in UI test')
            self.window.run_task('Synthetic cancellation test', operation, lambda _: None, cancellable=True)
            self.window.cancel_button.emit('clicked')
            if not self.window.cancel_event.is_set() or old_event.is_set():
                raise RuntimeError('Cancel button did not target the current operation')
            self.result['cancel_targets_current_operation'] = True
            GLib.timeout_add(300, self.cancel_finished)
        except Exception as error:
            self.fail(error)
        return False

    def cancel_finished(self):
        if self.window.busy:
            return True
        self.result['cancel_finishes_worker'] = True
        self.save_and_quit()
        return False

    def fail(self, error):
        self.result['errors'].append(type(error).__name__ + ': ' + str(error))
        self.save_and_quit()

    def save_and_quit(self):
        (ROOT / 'logs/gui-smoke.json').write_text(json.dumps(self.result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(self.result, ensure_ascii=False), flush=True)
        self.quit()


app = Smoke()
app.run([sys.argv[0]])
raise SystemExit(bool(app.result['errors']))
