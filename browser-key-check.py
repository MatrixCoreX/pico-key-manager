"""Check real browser WebAuthn behavior using a disposable localhost credential."""
import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time

from fido2.attestation import Attestation
from fido2.server import Fido2Server
from fido2.webauthn import RegistrationResponse
from pico_key_manager.device import describe, discover

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / 'logs/browser-key-check.json'


class Check:
    def __init__(self):
        refs = discover()
        if len(refs) != 1:
            raise ValueError('请只连接需要检查的一把 Pico。')
        self.device = describe(refs[0])
        self.server = Fido2Server(
            {'id': 'localhost', 'name': 'Pico USB 本地检查'},
            attestation='direct', verify_attestation=self.verify_attestation,
        )
        self.result = {'completed': False, 'stage': 'waiting_for_browser',
                       'device_usb_id': self.device['usb_id'],
                       'pin_set': self.device['pin_set'],
                       'openai_eligibility_tested': False}
        self.credential = None
        self.state = None
        self.deadline = 0
        self.save()

    def save(self):
        self.result['updated_at'] = datetime.now().astimezone().isoformat()
        REPORT.parent.mkdir(exist_ok=True)
        temporary = REPORT.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.result, ensure_ascii=False, indent=2) + '\n')
        os.replace(temporary, REPORT)

    def verify_attestation(self, attestation, client_data_hash):
        verified = Attestation.for_type(attestation.fmt)().verify(
            attestation.att_stmt, attestation.auth_data, client_data_hash,
        )
        self.result['attestation_type'] = verified.attestation_type.name
        # A valid signature is not manufacturer trust or platform eligibility.
        self.result['manufacturer_trust_verified'] = False

    def begin(self, action):
        self.deadline = time.monotonic() + 180
        if action == 'register':
            self.credential = None
            self.result = {key: value for key, value in self.result.items()
                           if key in ('device_usb_id', 'pin_set', 'openai_eligibility_tested')}
            self.result.update(completed=False, stage='waiting_for_usb_registration')
            options, self.state = self.server.register_begin(
                {'id': os.urandom(16), 'name': 'disposable-local-test'},
                authenticator_attachment='cross-platform',
                resident_key_requirement='discouraged', user_verification='required',
                extensions={'credProps': True},
            )
            options = dict(options)
            options['publicKey']['pubKeyCredParams'] = [{'type': 'public-key', 'alg': -7}]
        else:
            if self.credential is None:
                raise ValueError('请先完成第一步 USB 注册检查。')
            self.result['stage'] = 'waiting_for_usb_authentication'
            options, self.state = self.server.authenticate_begin(
                [self.credential], user_verification='required',
            )
            options = dict(options)
            options['publicKey']['allowCredentials'][0]['transports'] = ['usb']
        options['publicKey']['timeout'] = 120000
        self.save()
        return options

    def finish(self, action, response):
        expected_stage = 'waiting_for_usb_registration' if action == 'register' else 'waiting_for_usb_authentication'
        if not self.state or time.monotonic() > self.deadline or self.result['stage'] != expected_stage:
            raise ValueError('检查已过期，请重新开始当前步骤。')
        state, self.state = self.state, None
        if action == 'register':
            parsed = RegistrationResponse.from_dict(response)
            auth_data = self.server.register_complete(state, parsed)
            self.credential = auth_data.credential_data
            if self.credential is None:
                raise ValueError('设备未返回凭据公钥。')
            flags = int(auth_data.flags)
            attachment = response.get('authenticatorAttachment')
            transports = response.get('browserTransports', [])
            self.result.update(
                stage='registration_checked', attachment=attachment, transports=transports,
                aaguid=str(self.credential.aaguid),
                aaguid_matches_device=str(self.credential.aaguid) == self.device['aaguid'],
                user_present=bool(flags & 1), user_verified=bool(flags & 4),
                backup_eligible=bool(flags & 8), backed_up=bool(flags & 16),
                attestation_format=parsed.response.attestation_object.fmt,
                has_attestation_certificate=bool(parsed.response.attestation_object.att_stmt.get('x5c')),
                resident_key=parsed.client_extension_results.get('credProps', {}).get('rk'),
                external_usb_reported=attachment == 'cross-platform' and 'usb' in transports,
            )
        else:
            self.server.authenticate_complete(state, [self.credential], response)
            self.result.update(completed=True, stage='completed', signature_verified=True)
            self.credential = None
        self.save()
        return self.result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    check = Check()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, data, content_type='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') == f'localhost:{self.server.server_port}'

        def do_GET(self):
            if not self.valid_host() or self.path != '/':
                self.send(404, {'error': 'Not found'})
                return
            page = (ROOT / 'assets/browser-key-check.html').read_text()
            self.send(200, page.replace('__LOCAL_TOKEN__', token).encode(), 'text/html; charset=utf-8')

        def do_POST(self):
            origin = f'http://localhost:{self.server.server_port}'
            if (not self.valid_host() or self.headers.get('Origin') != origin
                    or self.headers.get('X-Local-Check') != token
                    or self.headers.get('Content-Type') != 'application/json'):
                self.send(403, {'error': 'Forbidden'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(size))
                action = data.get('action')
                if action not in ('register', 'authenticate'):
                    raise ValueError('Invalid action')
                if self.path == '/begin':
                    result = check.begin(action)
                elif self.path == '/finish':
                    result = check.finish(action, data['credential'])
                else:
                    self.send(404, {'error': 'Not found'})
                    return
                self.send(200, result)
            except Exception as error:
                # Do not log credentials, attestation bytes or exception details.
                self.send(400, {'error': '检查未完成，请重新开始。', 'type': type(error).__name__})

    server = HTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://localhost:{server.server_port}/'
    (ROOT / 'logs/browser-key-check-url.txt').write_text(url + '\n')
    print(url, flush=True)
    expiry = threading.Timer(1800, server.shutdown)
    expiry.daemon = True
    expiry.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        expiry.cancel()
        server.server_close()


if __name__ == '__main__':
    main()
