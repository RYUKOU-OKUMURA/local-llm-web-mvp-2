"""Local source tests; no real Ollama/Open WebUI/launchd/Tailscale is started."""
import copy
import json
import os
import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core as c

STATE = {'project': c.PROJECT, 'package_version': c.PACKAGE_VERSION,
         'fqdn': 'fixture.tail123.ts.net', 'web_port': 3000, 'https_port': 8443,
         'chat_model': 'qwen3:4b', 'embed_model': 'bge-m3:latest', 'phase': 'https',
         'installed': True, 'webui_version': c.OPENWEBUI_VERSION, 'published': False,
         'tailscale_bin': '/fixture/tailscale', 'ollama_bin': '/fixture/ollama'}
BEFORE = {'TCP': {'443': {'HTTPS': True}}, 'Web': {
    'fixture.tail123.ts.net:443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:18789'}}}}}
GOOD_AUTH = {'version': c.OPENWEBUI_VERSION, 'features': {'auth': True, 'enable_signup': False, 'enable_login_form': True, 'auth_trusted_header': False}}


def with_route():
    after = copy.deepcopy(BEFORE)
    endpoint, route = c.expected_route(STATE)
    after['TCP']['8443'] = {'HTTPS': True}
    after['Web'][endpoint] = route
    return after


class Parameters(unittest.TestCase):
    def test_version_exact(self): self.assertEqual(c.version('0.11.3'), '0.11.3')
    def test_version_floating_rejected(self):
        for value in ('latest', 'main', '0.11', '0.11.3;echo bad', '0.11.3rc1'):
            with self.subTest(value=value), self.assertRaises(c.SafetyError): c.version(value)
    def test_valid_models(self):
        for tag in ('qwen3:4b', 'bge-m3:latest', 'library/qwen3:4b'):
            self.assertEqual(c.model_tag(tag), tag)
    def test_cloud_model_name_rejected(self):
        for tag in ('gpt-oss:120b-cloud', 'MODEL:Cloud', 'https://example.com/model', '../x', 'x;touch /tmp/y', 'x$(id)'):
            with self.subTest(tag=tag), self.assertRaises(c.SafetyError): c.model_tag(tag)
    def test_ports(self): c.validate_ports(3000, 8443)
    def test_protected_ports(self):
        for ports in ((3000, 443), (11434, 8443), (18789, 8443), (3000, 3000), (0, 8443), (3000, 65536)):
            with self.subTest(ports=ports), self.assertRaises(c.SafetyError): c.validate_ports(*ports)
    def test_fqdn_from_running_tailnet(self):
        self.assertEqual(c.fqdn_from_status({'BackendState': 'Running', 'Self': {'DNSName': 'Fixture.tail123.ts.net.'}}), STATE['fqdn'])
    def test_fqdn_requires_running(self):
        with self.assertRaises(c.SafetyError): c.fqdn_from_status({'BackendState': 'Stopped', 'Self': {'DNSName': STATE['fqdn']}})
    def test_fqdn_injection(self):
        for name in ('host.example.org', 'x..ts.net', 'x.ts.net;echo bad'):
            with self.subTest(name=name), self.assertRaises(c.SafetyError): c.fqdn_from_status({'BackendState': 'Running', 'Self': {'DNSName': name}})
    def test_cloud_metadata(self):
        c.no_cloud_model({'details': {'family': 'qwen3'}})
        with self.assertRaises(c.SafetyError): c.no_cloud_model({'remote_host': 'https://example.org'})
    def test_cloud_remote_model(self):
        with self.assertRaises(c.SafetyError): c.no_cloud_model({'remote_model': 'something'})


class PrivateFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def test_env_round_trip_without_shell(self):
        values = {'SECRET': 's$e"c\\r$(touch /tmp/never-run)', 'DATA_DIR': '/Users/テスト/Library/Application Support/Local'}
        path = self.root / 'test.env'; c.write_env(path, values)
        self.assertEqual(c.read_env(path), values)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
    def test_env_shell_source_not_accepted(self):
        path = self.root / 'test.env'; c.atomic_write(path, 'A=$(touch bad)\n')
        with self.assertRaises(c.SafetyError): c.read_env(path)
    def test_env_duplicate(self):
        path = self.root / 'test.env'; c.atomic_write(path, 'A="one"\nA="two"\n')
        with self.assertRaises(c.SafetyError): c.read_env(path)
    def test_env_bad_value(self):
        for value in ('false', '32', '["x"]', '"a\\nb"'):
            path = self.root / 'test.env'; c.atomic_write(path, 'A=' + value + '\n')
            with self.subTest(value=value), self.assertRaises(c.SafetyError): c.read_env(path)
    def test_env_world_readable_rejected(self):
        path = self.root / 'test.env'; c.write_env(path, {'A': 'secret'}); path.chmod(0o644)
        with self.assertRaises(c.SafetyError): c.read_env(path)
    def test_symlink_write_rejected(self):
        target = self.root / 'target'; target.write_text('unchanged')
        path = self.root / 'test.env'; path.symlink_to(target)
        with self.assertRaises(c.SafetyError): c.write_env(path, {'A': 'secret'})
        self.assertEqual(target.read_text(), 'unchanged')
    def test_json_private(self):
        path = self.root / 'state.json'; c.write_json(path, {'ok': True}); self.assertEqual(c.read_json(path), {'ok': True})
    def test_json_not_object(self):
        path = self.root / 'state.json'; c.atomic_write(path, '[]')
        with self.assertRaises(c.SafetyError): c.read_json(path)
    def test_lock_exclusive(self):
        with c.lock(self.root / 'lck'):
            with self.assertRaises(c.SafetyError):
                with c.lock(self.root / 'lck'): pass
    def test_lock_released(self):
        with c.lock(self.root / 'lck'): pass
        with c.lock(self.root / 'lck'): pass


class Policy(unittest.TestCase):
    def test_final_values(self):
        env = c.web_env(Path('/safe root'), STATE, 'x' * 64, False)
        self.assertEqual(env['CORS_ALLOW_ORIGIN'], 'https://fixture.tail123.ts.net:8443')
        self.assertEqual(env['WEBUI_AUTH_COOKIE_SECURE'], 'true')
        self.assertEqual(env['ENABLE_SIGNUP'], 'false')
        self.assertEqual(env['ENABLE_PERSISTENT_CONFIG'], 'true')
        self.assertEqual(env['RAG_EMBEDDING_ENGINE'], 'ollama')
        self.assertEqual(env['OLLAMA_BASE_URLS'], c.OLLAMA_URL)
    def test_local_bootstrap(self):
        env = c.web_env(Path('/safe root'), STATE, 'x' * 64, True)
        self.assertEqual(env['CORS_ALLOW_ORIGIN'], 'http://127.0.0.1:3000')
        self.assertEqual(env['WEBUI_AUTH_COOKIE_SECURE'], 'false')
        self.assertEqual(env['ENABLE_SIGNUP'], 'true')
        self.assertEqual(env['WEBUI_AUTH'], 'true')
    def test_external_features_disabled(self):
        policy = c.policy(STATE)
        for key in ('ENABLE_OPENAI_API', 'ENABLE_WEB_SEARCH', 'ENABLE_CODE_EXECUTION', 'ENABLE_CODE_INTERPRETER', 'ENABLE_PLUGINS', 'ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS'):
            with self.subTest(key=key): self.assertIs(policy[key], False)
        self.assertEqual(policy['TOOL_SERVER_CONNECTIONS'], [])
    def test_ollama_no_cloud_and_single_load(self):
        env = c.ollama_env()
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1'); self.assertEqual(env['OLLAMA_HOST'], '127.0.0.1:11434')
        self.assertEqual(env['OLLAMA_NUM_PARALLEL'], '1'); self.assertEqual(env['OLLAMA_MAX_LOADED_MODELS'], '1')
    def test_inherited_secrets_not_forwarded(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'DO-NOT-PASS', 'HTTP_PROXY': 'http://bad', 'PYTHONPATH': '/unsafe'}):
            env = c.clean_env({'ONLY': 'x'})
            for key in ('OPENAI_API_KEY', 'HTTP_PROXY', 'PYTHONPATH'): self.assertNotIn(key, env)
    def test_extensions_encoding(self):
        self.assertEqual(c.as_env(c.policy(STATE))['RAG_ALLOWED_FILE_EXTENSIONS'], 'txt,md,pdf')
    def test_no_secret_rotation(self):
        self.assertEqual(c.web_env(Path('/x'), STATE, 'persist-me', False)['WEBUI_SECRET_KEY'], 'persist-me')
    def test_public_auth_good(self): c.public_auth_guard(GOOD_AUTH)
    def test_public_auth_reject_onboarding(self):
        with self.assertRaises(c.SafetyError): c.public_auth_guard(dict(GOOD_AUTH, onboarding=True))
    def test_public_auth_reject_signup(self):
        value = copy.deepcopy(GOOD_AUTH); value['features']['enable_signup'] = True
        with self.assertRaises(c.SafetyError): c.public_auth_guard(value)
    def test_public_auth_reject_no_auth(self):
        value = copy.deepcopy(GOOD_AUTH); value['features']['auth'] = False
        with self.assertRaises(c.SafetyError): c.public_auth_guard(value)
    def test_public_auth_reject_unknown(self):
        with self.assertRaises(c.SafetyError): c.public_auth_guard({})
    def test_public_auth_reject_trusted(self):
        value = copy.deepcopy(GOOD_AUTH); value['features']['auth_trusted_header'] = True
        with self.assertRaises(c.SafetyError): c.public_auth_guard(value)


class Persistence(unittest.TestCase):
    def test_legacy_ast_without_execution(self):
        source = 'raise Exception("must not run")\nENABLE_SIGNUP = PersistentConfig("ENABLE_SIGNUP", "ui.enable_signup", True)'
        self.assertEqual(c.extract_config_keys(source), {'ENABLE_SIGNUP': 'ui.enable_signup'})
    def test_current_ast(self):
        source = 'DEFAULT_CONFIG = {"ui.enable_signup": ENABLE_SIGNUP, "ollama.enable": ENABLE_OLLAMA_API}'
        self.assertEqual(c.extract_config_keys(source)['ENABLE_OLLAMA_API'], 'ollama.enable')
    def test_unknown_ast_empty(self): self.assertEqual(c.extract_config_keys('custom_loader()'), {})
    def test_nested_update_keeps_unrelated(self):
        before = {'ui': {'enable_signup': True, 'theme': 'dark'}, 'other': {'x': 7}}
        after = c.updated_config(before, {'ui.enable_signup': False})
        self.assertTrue(before['ui']['enable_signup']); self.assertEqual(after['ui']['theme'], 'dark')
        c.assert_config(after, {'ui.enable_signup': False})
    def test_flat_update_keeps_unrelated(self):
        before = {'ui.enable_signup': True, 'other': {'x': 7}}
        after = c.updated_config(before, {'ui.enable_signup': False})
        self.assertTrue(before['ui.enable_signup']); self.assertEqual(after['other'], {'x': 7})
        c.assert_config(after, {'ui.enable_signup': False})
    def test_missing_key_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_config({}, {'ui.enable_signup': False})
    def test_wrong_value_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_config({'ui.enable_signup': True}, {'ui.enable_signup': False})
    def test_wrong_type_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_config({'ui.enable_signup': 0}, {'ui.enable_signup': False})
    def test_bad_tree_rejected(self):
        with self.assertRaises(c.SafetyError): c.updated_config({'ui': 'wrong'}, {'ui.enable_signup': False})


class Network(unittest.TestCase):
    def test_existing_443_preserved(self):
        after = with_route(); c.assert_owned_route(after, STATE)
        self.assertEqual(c.without_own_route(after, STATE), BEFORE)
    def test_other_changes_detectable(self):
        after = with_route(); after['Web']['fixture.tail123.ts.net:443']['Handlers']['/']['Proxy'] = 'http://wrong'
        self.assertNotEqual(c.without_own_route(after, STATE), BEFORE)
    def test_foreground_collision_detected(self):
        self.assertTrue(c.port_references({'Foreground': {'sess': {'TCP': {'8443': {'HTTPS': True}}}}}, 8443))
    def test_no_collision(self): self.assertFalse(c.port_references(BEFORE, 8443))
    def test_route_not_ours_rejected(self):
        after = with_route(); after['Web'][c.expected_route(STATE)[0]]['Handlers']['/']['Proxy'] = 'http://other'
        with self.assertRaises(c.SafetyError): c.assert_owned_route(after, STATE)
    def test_extra_handler_rejected(self):
        after = with_route(); after['Web'][c.expected_route(STATE)[0]]['Handlers']['/other'] = {'Text': 'someone else'}
        with self.assertRaises(c.SafetyError): c.assert_owned_route(after, STATE)
    def test_funnel_rejected(self):
        after = with_route(); after['AllowFunnel'] = {c.expected_route(STATE)[0]: True}
        with self.assertRaises(c.SafetyError): c.assert_owned_route(after, STATE)
    def test_foreground_same_port_rejected(self):
        after = with_route(); after['Foreground'] = {'x': {'TCP': {'8443': {'HTTPS': True}}}}
        with self.assertRaises(c.SafetyError): c.assert_owned_route(after, STATE)
    def test_false_funnel_and_empty_maps_normalized(self):
        after = with_route(); after['AllowFunnel'] = {c.expected_route(STATE)[0]: False}
        self.assertEqual(c.without_own_route(after, STATE), BEFORE)
    def test_tcp_change_rejected(self):
        after = with_route(); after['TCP']['8443'] = {'TCPForward': '127.0.0.1:22'}
        with self.assertRaises(c.SafetyError): c.assert_owned_route(after, STATE)
    def test_listener_parse_and_allow(self):
        parsed = c.listener_records('p123\nn127.0.0.1:3000\n')
        c.assert_listener(parsed, 3000, 123)
    def test_listener_wildcard_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_listener({123: ['*:3000']}, 3000, 123)
    def test_listener_ipv6_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_listener({123: ['[::1]:3000']}, 3000, 123)
    def test_listener_wrong_pid_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_listener({456: ['127.0.0.1:3000']}, 3000, 123)
    def test_listener_multiple_pid_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_listener({123: ['127.0.0.1:3000'], 456: ['*:3000']}, 3000, 123)


class LaunchDefinition(unittest.TestCase):
    def test_plist_round_trip(self):
        root = Path('/Users/日本語/Library/Application Support/local-llm-web')
        value = c.launch_plist(root, 'webui')
        self.assertEqual(plistlib.loads(plistlib.dumps(value)), value)
        self.assertEqual(value['ProgramArguments'], ['/bin/bash', str(root / 'scripts/start-webui.sh')])
        self.assertEqual(value['KeepAlive'], {'SuccessfulExit': False})
        self.assertEqual(value['Label'], 'local.llmweb.webui')
        self.assertNotIn('UserName', value)
    def test_unknown_service(self):
        with self.assertRaises(c.SafetyError): c.launch_plist(Path('/x'), 'openclaw')
    def test_relative_path_rejected(self):
        with self.assertRaises(c.SafetyError): c.launch_plist(Path('relative'), 'webui')


class Backup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name) / 'app'; self.root.mkdir(mode=0o700)
        for name in ('data', 'config', 'scripts', 'records', 'backups'): (self.root / name).mkdir(mode=0o700)
        db = sqlite3.connect(self.root / 'data/webui.db'); db.execute('CREATE TABLE sample (text TEXT)'); db.execute('INSERT INTO sample VALUES (?)', ('架空会話',)); db.commit(); db.close()
        (self.root / 'data/uploads').mkdir(); (self.root / 'data/uploads/test.md').write_text('架空資料')
        (self.root / 'data/vector_db').mkdir(); (self.root / 'data/vector_db/index.bin').write_bytes(b'fixture')
        c.write_env(self.root / 'config/webui.env', {'WEBUI_SECRET_KEY': 'fixture-secret'})
        self.snapshot = self.root / 'backups/example'
    def tearDown(self): self.temp.cleanup()
    def make(self): c.create_snapshot(self.root, self.snapshot, c.OPENWEBUI_VERSION)
    def test_full_snapshot_and_verify(self):
        self.make(); manifest = c.verify_snapshot(self.snapshot)
        self.assertIn('data/uploads/test.md', manifest['files'])
        self.assertIn('data/vector_db/index.bin', manifest['files'])
        self.assertEqual(c.read_env(self.snapshot / 'config/webui.env')['WEBUI_SECRET_KEY'], 'fixture-secret')
    def test_corrupt_file_rejected(self):
        self.make(); (self.snapshot / 'data/uploads/test.md').write_text('tampered')
        with self.assertRaises(c.SafetyError): c.verify_snapshot(self.snapshot)
    def test_extra_file_rejected(self):
        self.make(); (self.snapshot / 'extra').write_text('tampered')
        with self.assertRaises(c.SafetyError): c.verify_snapshot(self.snapshot)
    def test_missing_file_rejected(self):
        self.make(); (self.snapshot / 'data/uploads/test.md').unlink()
        with self.assertRaises(c.SafetyError): c.verify_snapshot(self.snapshot)
    def test_symlink_rejected(self):
        (self.root / 'data/escape').symlink_to('/etc/passwd')
        with self.assertRaises(c.SafetyError): self.make()
    def test_duplicate_destination_rejected(self):
        self.make()
        with self.assertRaises(c.SafetyError): self.make()
    def test_missing_database(self):
        (self.root / 'data/webui.db').unlink()
        with self.assertRaises(c.SafetyError): self.make()
    def test_staged_restore_does_not_change_original(self):
        self.make(); original = c.tree_hashes(self.root / 'data'); dest = self.root / 'restore'
        c.stage_snapshot(self.snapshot, dest, c.OPENWEBUI_VERSION)
        (dest / 'data/uploads/test.md').write_text('only copy changed')
        self.assertEqual(original, c.tree_hashes(self.root / 'data'))
    def test_restore_version_mismatch(self):
        self.make()
        with self.assertRaises(c.SafetyError): c.stage_snapshot(self.snapshot, self.root / 'restore', '0.0.1')
    def test_restore_no_overwrite(self):
        self.make(); dest = self.root / 'restore'; dest.mkdir()
        with self.assertRaises(c.SafetyError): c.stage_snapshot(self.snapshot, dest, c.OPENWEBUI_VERSION)




class BootstrapExposure(unittest.TestCase):
    def test_different_port_existing_proxy_rejected(self):
        config = {'Web': {'fixture.tail123.ts.net:443': {'Handlers': {'/local': {'Proxy': 'http://127.0.0.1:3000'}}}}}
        with self.assertRaises(c.SafetyError): c.assert_backend_not_shared(config, 3000)
    def test_existing_openclaw_route_allowed(self): c.assert_backend_not_shared(BEFORE, 3000)
    def test_tcp_forward_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_backend_not_shared({'TCP': {'9000': {'TCPForward': 'localhost:3000'}}}, 3000)
    def test_nested_list_reference(self):
        self.assertTrue(c.port_references({'services': [{'Web': {'fixture:8443': {}}}]}, 8443))
    def test_unknown_auth_response_rejected(self):
        with self.assertRaises(c.SafetyError): c.public_auth_guard(['not a config'])
    def test_unknown_model_response_rejected(self):
        with self.assertRaises(c.SafetyError): c.no_cloud_model(['not metadata'])


class DatabaseStartupGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / 'webui.db'
    def tearDown(self): self.temp.cleanup()
    def make(self, roles):
        connection = sqlite3.connect(self.path)
        connection.execute('CREATE TABLE user (role TEXT)')
        connection.executemany('INSERT INTO user VALUES (?)', [(r,) for r in roles])
        connection.commit(); connection.close(); self.path.chmod(0o600)
    def test_admin_present_no_changes(self):
        self.make(['admin']); before = c.file_hash(self.path)
        c.assert_admin_database(self.path); self.assertEqual(before, c.file_hash(self.path))
    def test_missing_database_rejected(self):
        with self.assertRaises(c.SafetyError): c.assert_admin_database(self.path)
        self.assertFalse(self.path.exists())
    def test_empty_users_rejected(self):
        self.make([])
        with self.assertRaises(c.SafetyError): c.assert_admin_database(self.path)
    def test_no_admin_rejected(self):
        self.make(['pending'])
        with self.assertRaises(c.SafetyError): c.assert_admin_database(self.path)
    def test_multiple_users_rejected(self):
        self.make(['admin', 'user'])
        with self.assertRaises(c.SafetyError): c.assert_admin_database(self.path)
    def test_unknown_schema_rejected(self):
        connection = sqlite3.connect(self.path); connection.execute('CREATE TABLE other (x INTEGER)'); connection.close(); self.path.chmod(0o600)
        with self.assertRaises(c.SafetyError): c.assert_admin_database(self.path)

if __name__ == '__main__': unittest.main()

class ExistingOllama(unittest.TestCase):
    def test_existing_loopback_listener_allowed(self):
        c.assert_loopback_listener({123: ['127.0.0.1:11434']}, 11434)

    def test_existing_wildcard_listener_rejected(self):
        with self.assertRaises(c.SafetyError):
            c.assert_loopback_listener({123: ['*:11434']}, 11434)

    def test_existing_ipv6_wildcard_listener_rejected(self):
        with self.assertRaises(c.SafetyError):
            c.assert_loopback_listener({123: ['*:11434', '[::]:11434']}, 11434)

    def test_existing_multiple_loopback_pids_allowed_for_validation_only(self):
        c.assert_loopback_listener({123: ['127.0.0.1:11434'], 456: ['127.0.0.1:11434']}, 11434)
