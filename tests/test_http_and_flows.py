"""HTTP fixture + orchestration mock tests (NOT upstream product E2E tests)."""
import argparse
import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch, Mock

import core as c
import llmweb as app
from test_safety import STATE, BEFORE, GOOD_AUTH, with_route


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass
    def send_json(self, status, data):
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    def do_GET(self):
        if self.path == '/api/config': self.send_json(200, GOOD_AUTH)
        elif self.path == '/api/v1/configs/export':
            if self.headers.get('Authorization') == 'Bearer fixture-token': self.send_json(200, {'ui.enable_signup': False})
            else: self.send_json(401, {'error': 'private fixture error'})
        elif self.path == '/redirect':
            self.send_response(302); self.send_header('Location', '/must-not-follow'); self.end_headers()
        else: self.send_json(404, {'error': 'missing'})
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/api/v1/auths/signin' and data == {'email': 'fixture@example.test', 'password': 'fixture-password'}:
            self.send_json(200, {'token': 'fixture-token', 'role': 'admin'})
        elif self.path == '/api/v1/configs/import' and self.headers.get('Authorization') == 'Bearer fixture-token':
            self.send_json(200, data['config'])
        else: self.send_json(403, {'error': 'private fixture error'})


class HTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def test_public_fixture(self): c.public_auth_guard(c.require_api(self.base, '/api/config'))
    def test_unauthorized_code_no_error_body(self):
        code, body, _ = c.api(self.base, '/api/v1/configs/export'); self.assertEqual(code, 401); self.assertIsNone(body)
    def test_admin_token_not_printed(self):
        stream = io.StringIO()
        with patch('builtins.input', return_value='fixture@example.test'), patch('getpass.getpass', return_value='fixture-password'), contextlib.redirect_stdout(stream):
            token = app.admin_token(self.base)
        self.assertEqual(token, 'fixture-token'); self.assertNotIn('fixture-token', stream.getvalue()); self.assertNotIn('fixture-password', stream.getvalue())
    def test_export_import_shape(self):
        exported = c.require_api(self.base, '/api/v1/configs/export', token='fixture-token')
        self.assertEqual(c.require_api(self.base, '/api/v1/configs/import', {'config': exported}, token='fixture-token'), exported)
    def test_redirect_blocked(self):
        with self.assertRaises(c.SafetyError): c.api(self.base, '/redirect', token='fixture-token')
    def test_external_http_rejected(self):
        with self.assertRaises(c.SafetyError): c.api('http://example.test', '/api')
    def test_external_https_rejected(self):
        with self.assertRaises(c.SafetyError): c.api('https://example.test', '/api')
    def test_double_slash_rejected(self):
        with self.assertRaises(c.SafetyError): c.api(self.base, '//example.test')
    def test_failed_api_generic_error(self):
        with self.assertRaises(c.SafetyError) as error: c.require_api(self.base, '/nope')
        self.assertIn('HTTP 404', str(error.exception)); self.assertNotIn('private', str(error.exception))


class Flows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        for name in ('config', 'scripts', 'records', 'data', 'logs', 'backups'): (self.root / name).mkdir(mode=0o700)
        self.state = copy.deepcopy(STATE)
        c.write_json(self.root / 'config/state.json', self.state)
        c.write_env(self.root / 'config/webui.env', c.web_env(self.root, self.state, 'z' * 64, False))
        c.write_env(self.root / 'config/ollama.env', c.ollama_env())
        mapping = {k: 'fixture.' + k for k in c.policy(self.state)}
        c.write_json(self.root / 'config/config-key-map.json', mapping)
        self.mapping = mapping
        self.patchers = [patch.object(app, 'ROOT', self.root), patch.object(app, 'mac_only'),
                         patch.object(app, 'ensure_running'), patch.object(app, 'models_ready'),
                         patch.object(app, 'port_free'), patch.object(app, 'admin_token', return_value='fixture-token'),
                         patch.object(app, 'confirm'), patch.object(app, 'command'),
                         patch.object(app, 'stop_service', return_value=True), patch.object(app, 'start_service')]
        self.mocks = [p.start() for p in self.patchers]
        self.command = self.mocks[7]; self.stop_service = self.mocks[8]
    def tearDown(self):
        for p in reversed(self.patchers): p.stop()
        self.temp.cleanup()
    def test_secure_persists_no_signup_before_phase_change(self):
        self.state['phase'] = 'local'; c.write_json(self.root / 'config/state.json', self.state)
        c.write_env(self.root / 'config/webui.env', c.web_env(self.root, self.state, 'z' * 64, True))
        config = {value: c.policy(self.state)[key] for key, value in self.mapping.items()}
        config[self.mapping['ENABLE_SIGNUP']] = True; config['user.preference'] = 'keep'
        def fake(base, path, body=None, token=None, **kw):
            if path == '/api/v1/configs/export': return copy.deepcopy(config)
            if path == '/api/v1/configs/import': config.clear(); config.update(body['config']); return config
            if path == '/api/config': return GOOD_AUTH
            raise AssertionError(path)
        with patch.object(app, 'ts_json', return_value=BEFORE), patch.object(app, 'require_api', side_effect=fake): app.secure()
        self.assertFalse(config[self.mapping['ENABLE_SIGNUP']]); self.assertEqual(config['user.preference'], 'keep')
        self.assertEqual(c.read_json(self.root / 'config/state.json')['phase'], 'https')
        self.assertEqual(c.read_env(self.root / 'config/webui.env')['WEBUI_SECRET_KEY'], 'z' * 64)
    def test_publish_preserves_443_and_uses_explicit_port(self):
        def status(_state, *args):
            if args == ('status',): return {'BackendState': 'Running', 'Self': {'DNSName': STATE['fqdn']}}
            if not hasattr(status, 'n'): status.n = 0
            status.n += 1
            return BEFORE if status.n == 1 else with_route()
        with patch.object(app, 'ts_json', side_effect=status), patch.object(app, 'require_api', return_value=GOOD_AUTH), patch.object(app, 'verify_admin_config'):
            app.publish(argparse.Namespace(yes=True))
        self.assertEqual(self.command.call_args.args[0], ['/fixture/tailscale', 'serve', '--bg', '--https=8443', 'http://127.0.0.1:3000'])
        self.assertTrue(c.read_json(self.root / 'config/state.json')['published'])
    def test_publish_bootstrap_rejected_without_serve_write(self):
        self.state['phase'] = 'local'; c.write_json(self.root / 'config/state.json', self.state)
        with self.assertRaises(c.SafetyError): app.publish(argparse.Namespace(yes=True))
        self.command.assert_not_called()
    def test_publish_existing_port_rejected(self):
        def status(_state, *args):
            if args == ('status',): return {'BackendState': 'Running', 'Self': {'DNSName': STATE['fqdn']}}
            return with_route()
        with patch.object(app, 'ts_json', side_effect=status), patch.object(app, 'require_api', return_value=GOOD_AUTH), patch.object(app, 'verify_admin_config'):
            with self.assertRaises(c.SafetyError): app.publish(argparse.Namespace(yes=True))
        self.command.assert_not_called()
    def test_publish_change_to_existing_stops_only_webui(self):
        broken = with_route(); broken['TCP']['443'] = {'HTTPS': False}
        replies = iter([{'BackendState': 'Running', 'Self': {'DNSName': STATE['fqdn']}}, BEFORE, broken])
        with patch.object(app, 'ts_json', side_effect=lambda *a: next(replies)), patch.object(app, 'require_api', return_value=GOOD_AUTH), patch.object(app, 'verify_admin_config'):
            with self.assertRaises(c.SafetyError): app.publish(argparse.Namespace(yes=True))
        self.stop_service.assert_called_once_with(self.state | {'publication_pending': True}, 'webui')
        self.assertEqual(self.command.call_count, 1)  # Never restore/reset whole config.
        self.assertTrue(c.read_json(self.root / 'config/state.json')['publication_pending'])
    def test_unpublish_only_owned_route(self):
        self.state['published'] = True; c.write_json(self.root / 'config/state.json', self.state)
        with patch.object(app, 'ts_json', side_effect=[with_route(), BEFORE]): app.unpublish()
        self.assertEqual(self.command.call_args.args[0], ['/fixture/tailscale', 'serve', '--bg', '--https=8443', 'off'])
        self.assertFalse(c.read_json(self.root / 'config/state.json')['published'])
    def test_unpublish_without_receipt_rejected(self):
        with self.assertRaises(c.SafetyError): app.unpublish()
        self.command.assert_not_called()
    def test_unpublish_modified_foreign_route_rejected(self):
        self.state['published'] = True; c.write_json(self.root / 'config/state.json', self.state)
        different = with_route(); different['Web'][c.expected_route(STATE)[0]]['Handlers']['/'] = {'Proxy': 'http://someone-else'}
        with patch.object(app, 'ts_json', return_value=different):
            with self.assertRaises(c.SafetyError): app.unpublish()
        self.command.assert_not_called()
    def test_restore_cannot_use_production_port(self):
        with self.assertRaises(c.SafetyError): app.restore_test(argparse.Namespace(snapshot=str(self.root / 'backups/x'), port=3000, yes=True))
        self.stop_service.assert_not_called()
    def test_readonly_preflight_linux(self):
        with patch.object(app.sys, 'platform', 'linux'):
            result = app.preflight()
        self.assertFalse(result['mac_ready']); self.command.assert_not_called()


if __name__ == '__main__': unittest.main()
