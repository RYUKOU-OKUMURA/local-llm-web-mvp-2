"""Pure configuration/safety helpers and the thin native process runner.

No third-party imports, shell evaluation, database writes or remote inference.
"""
from __future__ import annotations

import ast
import copy
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROJECT = 'local-llm-web'
PACKAGE_VERSION = '0.1.1'
OPENWEBUI_VERSION = '0.11.3'  # PyPI listing checked 2026-09-16; not Mac-certified.
ROOT = Path.home() / 'Library' / 'Application Support' / PROJECT
OLLAMA_URL = 'http://127.0.0.1:11434'
LABELS = {'ollama': 'local.llmweb.ollama', 'webui': 'local.llmweb.webui'}


class SafetyError(RuntimeError):
    """An unmet precondition: stop, rather than weakening the configuration."""


def version(value: str) -> str:
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise SafetyError('バージョンは x.y.z の安定版番号で指定してください。latestは禁止です。')
    return value


def model_tag(value: str) -> str:
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,149}', value)
            or '://' in value or '..' in value or 'cloud' in value.lower()):
        raise SafetyError('ローカルOllamaモデルの正式タグを指定してください。URL・cloudタグは不可です。')
    return value


def validate_ports(web: int, https: int) -> None:
    if any(not isinstance(p, int) or not 1024 <= p <= 65535 for p in (web, https)):
        raise SafetyError('新規ポートは1024〜65535で指定してください。')
    if web == https or {web, https} & {11434, 18789}:
        raise SafetyError('Ollama・OpenClaw・新規入口同士のポートを重ねないでください。')


def fqdn_from_status(status: dict) -> str:
    name = str(status.get('Self', {}).get('DNSName', '')).rstrip('.').lower()
    if status.get('BackendState') != 'Running':
        raise SafetyError('TailscaleがRunningではありません。既存接続を先に確認してください。')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.ts\.net', name):
        raise SafetyError('Tailscale Self.DNSNameから正規のts.net名を確認できません。')
    if '..' in name or any(len(x) > 63 for x in name.split('.')):
        raise SafetyError('不正なTailscaleホスト名です。')
    return name


def private(path: Path, directory: bool = False) -> None:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise SafetyError(f'所有者専用の通常ファイル/フォルダではありません: {path}')
    if directory and not stat.S_ISDIR(st.st_mode):
        raise SafetyError(f'通常フォルダではありません: {path}')
    if not directory and not stat.S_ISREG(st.st_mode):
        raise SafetyError(f'通常ファイルではありません: {path}')


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise SafetyError(f'シンボリックリンクを上書きしません: {path}')
    temporary = path.with_name(path.name + f'.tmp-{os.getpid()}')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_json(path: Path, data: object) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def read_json(path: Path) -> dict:
    private(path)
    result = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result, dict):
        raise SafetyError(f'JSONオブジェクトではありません: {path.name}')
    return result


def write_env(path: Path, values: dict[str, str]) -> None:
    # Deliberately NOT sourced by a shell. JSON strings preserve $, quotes and spaces.
    text = '# local-llm-web: KEY="JSON string" / シェルsource禁止 / 秘密情報あり\n'
    text += ''.join(f'{key}={json.dumps(str(value), ensure_ascii=False)}\n'
                    for key, value in sorted(values.items()))
    atomic_write(path, text)


def read_env(path: Path) -> dict[str, str]:
    private(path)
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, val = line.partition('=')
        if not sep or not re.fullmatch('[A-Z][A-Z0-9_]*', key) or key in result:
            raise SafetyError(f'env形式が不正です: {path.name}')
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError as exc:
            raise SafetyError(f'env値はJSON文字列で記述してください: {key}') from exc
        if not isinstance(parsed, str) or '\x00' in parsed or '\n' in parsed or '\r' in parsed:
            raise SafetyError(f'env値が不正です: {key}')
        result[key] = parsed
    return result


def policy(state: dict) -> dict:
    """Values both seeded into env and reconciled with persisted WebUI config."""
    values = {key: False for key in (
        'ENABLE_SIGNUP', 'ENABLE_OPENAI_API', 'ENABLE_DIRECT_CONNECTIONS',
        'ENABLE_WEB_SEARCH', 'ENABLE_CODE_EXECUTION', 'ENABLE_CODE_INTERPRETER',
        'ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS', 'ENABLE_PLUGINS',
        'ENABLE_IMAGE_GENERATION', 'ENABLE_AUTOMATIONS', 'ENABLE_SUBAGENTS',
        'ENABLE_MEMORIES', 'ENABLE_CHANNELS', 'ENABLE_CALENDAR', 'ENABLE_NOTES',
        'ENABLE_COMMUNITY_SHARING', 'ENABLE_USER_WEBHOOKS', 'ENABLE_GOOGLE_DRIVE_INTEGRATION',
        'ENABLE_ONEDRIVE_INTEGRATION', 'ENABLE_RAG_HYBRID_SEARCH', 'PDF_EXTRACT_IMAGES',
        'ENABLE_ASYNC_EMBEDDING', 'ENABLE_VERSION_UPDATE_CHECK',
        'RAG_EMBEDDING_MODEL_AUTO_UPDATE', 'RAG_RERANKING_MODEL_AUTO_UPDATE',
        'RAG_EMBEDDING_MODEL_TRUST_REMOTE_CODE', 'RAG_RERANKING_MODEL_TRUST_REMOTE_CODE',
    )}
    values.update({
        'WEBUI_URL': f'https://{state["fqdn"]}:{state["https_port"]}',
        'ENABLE_LOGIN_FORM': True, 'ENABLE_OLLAMA_API': True,
        'OLLAMA_BASE_URLS': [OLLAMA_URL], 'OLLAMA_API_CONFIGS': {},
        'TOOL_SERVER_CONNECTIONS': [], 'TERMINAL_SERVER_CONNECTIONS': [],
        'RAG_EMBEDDING_ENGINE': 'ollama', 'RAG_EMBEDDING_MODEL': state['embed_model'],
        'RAG_OLLAMA_BASE_URL': OLLAMA_URL, 'CONTENT_EXTRACTION_ENGINE': '',
        'RAG_RERANKING_MODEL': '', 'RAG_FILE_MAX_SIZE': 10, 'RAG_FILE_MAX_COUNT': 5,
        'RAG_ALLOWED_FILE_EXTENSIONS': ['txt', 'md', 'pdf'],
        'RAG_EMBEDDING_BATCH_SIZE': 1, 'RAG_TEXT_SPLITTER': '',
    })
    return values


# These must have a discoverable persistence key in the installed release.
REQUIRED_KEYS = {
    'WEBUI_URL', 'ENABLE_SIGNUP', 'ENABLE_LOGIN_FORM', 'ENABLE_OLLAMA_API',
    'OLLAMA_BASE_URLS', 'OLLAMA_API_CONFIGS', 'ENABLE_DIRECT_CONNECTIONS',
    'ENABLE_OPENAI_API', 'ENABLE_WEB_SEARCH',
    'ENABLE_CODE_EXECUTION', 'ENABLE_CODE_INTERPRETER', 'RAG_EMBEDDING_ENGINE',
    'RAG_EMBEDDING_MODEL', 'RAG_OLLAMA_BASE_URL', 'CONTENT_EXTRACTION_ENGINE',
    'TOOL_SERVER_CONNECTIONS', 'RAG_FILE_MAX_SIZE', 'RAG_FILE_MAX_COUNT',
}


def as_env(values: dict) -> dict[str, str]:
    output = {}
    for key, val in values.items():
        if isinstance(val, bool):
            output[key] = str(val).lower()
        elif key == 'OLLAMA_BASE_URLS':
            output[key] = ';'.join(val)
        elif key == 'RAG_ALLOWED_FILE_EXTENSIONS':
            output[key] = ','.join(val)
        elif isinstance(val, (dict, list)):
            output[key] = json.dumps(val, separators=(',', ':'))
        else:
            output[key] = str(val)
    return output


def web_env(root: Path, state: dict, secret: str, local: bool) -> dict[str, str]:
    env = as_env(policy(state))
    origin = (f'http://127.0.0.1:{state["web_port"]}' if local else env['WEBUI_URL'])
    env.update({
        'DATA_DIR': str(root / 'data'), 'WEBUI_SECRET_KEY': secret,
        'CORS_ALLOW_ORIGIN': origin, 'ENABLE_SIGNUP': str(local).lower(),
        'WEBUI_AUTH': 'true', 'ENABLE_PERSISTENT_CONFIG': 'true',
        'WEBUI_SESSION_COOKIE_SECURE': str(not local).lower(),
        'WEBUI_AUTH_COOKIE_SECURE': str(not local).lower(),
        'WEBUI_SESSION_COOKIE_SAME_SITE': 'lax', 'WEBUI_AUTH_COOKIE_SAME_SITE': 'lax',
        'SAFE_MODE': 'true', 'UVICORN_WORKERS': '1', 'VECTOR_DB': 'chroma',
        'STORAGE_PROVIDER': 'local', 'HF_HUB_OFFLINE': '1', 'OFFLINE_MODE': 'true',
        'ANONYMIZED_TELEMETRY': 'false', 'DO_NOT_TRACK': 'true',
        'SCARF_NO_ANALYTICS': 'true', 'DEFAULT_LOCALE': 'ja-JP',
        'DEFAULT_MODELS': state['chat_model'], 'PYTHONNOUSERSITE': '1',
        'TIKTOKEN_CACHE_DIR': str(root / 'data' / 'cache' / 'tiktoken'),
    })
    return env


def ollama_env() -> dict[str, str]:
    return {'OLLAMA_HOST': '127.0.0.1:11434', 'OLLAMA_NO_CLOUD': '1',
            'OLLAMA_NUM_PARALLEL': '1', 'OLLAMA_MAX_LOADED_MODELS': '1',
            'OLLAMA_CONTEXT_LENGTH': '4096'}


def clean_env(extra: dict[str, str]) -> dict[str, str]:
    # No inherited cloud keys, proxies, PYTHONPATH or trusted-auth header settings.
    env = {key: os.environ[key] for key in ('HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR')
           if key in os.environ}
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin'
    env.update(extra)
    return env


def extract_config_keys(source: str) -> dict[str, str]:
    """Read installed source WITHOUT importing it / causing database migrations.

    Supports classic PersistentConfig declarations and current DEFAULT_CONFIG maps.
    Unknown release layouts fail closed at the caller.
    """
    tree = ast.parse(source)
    keys = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        val = node.value
        if isinstance(val, ast.Call) and len(val.args) >= 2:
            func = val.func.id if isinstance(val.func, ast.Name) else ''
            if func in ('PersistentConfig', 'ConfigVar'):
                a, b = val.args[:2]
                if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
                    if isinstance(a.value, str) and isinstance(b.value, str):
                        keys[a.value] = b.value
        if 'DEFAULT_CONFIG' in names and isinstance(val, ast.Dict):
            for k, v in zip(val.keys, val.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and isinstance(v, ast.Name):
                    keys[v.id] = k.value
    return keys


def get_config(config: dict, key: str):
    if key in config:
        return config[key]
    value = config
    for segment in key.split('.'):
        if not isinstance(value, dict) or segment not in value:
            raise SafetyError(f'保存設定に必須キーがありません: {key}')
        value = value[segment]
    return value


def updated_config(config: dict, updates: dict) -> dict:
    result = copy.deepcopy(config)
    flat = any('.' in key for key in result)
    for key, val in updates.items():
        if flat:
            result[key] = val
        else:
            current = result
            parts = key.split('.')
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                if not isinstance(current[part], dict):
                    raise SafetyError(f'設定構造が想定と異なります: {key}')
                current = current[part]
            current[parts[-1]] = val
    return result


def assert_config(config: dict, expected: dict) -> None:
    mismatches = [key for key, value in expected.items()
                  if get_config(config, key) != value or type(get_config(config, key)) is not type(value)]
    if mismatches:
        raise SafetyError('安全設定の読戻しが一致しません: ' + ', '.join(mismatches))


def normalized(value):
    if isinstance(value, dict):
        result = {k: normalized(v) for k, v in value.items()}
        return {k: v for k, v in result.items() if v is not None and v != {}}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def port_references(config: dict, port: int) -> bool:
    """Conservative recursive scan includes Foreground / Services blocks."""
    if isinstance(config, list):
        return any(port_references(item, port) for item in config)
    if not isinstance(config, dict):
        return False
    for key, val in config.items():
        if str(key) == str(port) or str(key).endswith(':' + str(port)):
            return True
        if isinstance(val, (dict, list)) and port_references(val, port):
            return True
    return False


def assert_no_public_funnel(config: dict, endpoint: str) -> None:
    for key, val in config.items():
        if key == 'AllowFunnel' and isinstance(val, dict) and val.get(endpoint):
            raise SafetyError('対象入口にFunnel設定があります。自動では変更しません。')
        if isinstance(val, dict):
            assert_no_public_funnel(val, endpoint)


def expected_route(state: dict) -> tuple[str, dict]:
    return (f'{state["fqdn"]}:{state["https_port"]}',
            {'Handlers': {'/': {'Proxy': f'http://127.0.0.1:{state["web_port"]}'}}})


def assert_owned_route(config: dict, state: dict) -> None:
    endpoint, route = expected_route(state)
    assert_no_public_funnel(config, endpoint)
    if normalized((config.get('Web') or {}).get(endpoint)) != route:
        raise SafetyError('Serve入口が本パッケージ専用の経路と一致しません。変更しません。')
    if normalized((config.get('TCP') or {}).get(str(state['https_port']))) != {'HTTPS': True}:
        raise SafetyError('ServeのTCP設定が想定と異なります。変更しません。')
    for key, block in config.items():
        if key not in ('TCP', 'Web', 'AllowFunnel') and isinstance(block, dict):
            if port_references(block, state['https_port']):
                raise SafetyError('別のServeセッションも対象ポートを使用しています。')


def without_own_route(config: dict, state: dict) -> dict:
    result = copy.deepcopy(config)
    endpoint, _ = expected_route(state)
    for key, subkey in (('TCP', str(state['https_port'])), ('Web', endpoint), ('AllowFunnel', endpoint)):
        if isinstance(result.get(key), dict):
            result[key].pop(subkey, None)
    return normalized(result)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafetyError('管理APIのリダイレクトを拒否しました。認証情報を転送しません。')


def api(base: str, endpoint: str, body=None, token: str | None = None,
        timeout: int = 15) -> tuple[int, object, object]:
    """Only local HTTP management or explicitly named tailnet HTTPS; no proxy."""
    url = base.rstrip('/') + endpoint
    parsed = urlparse(base)
    if not ((parsed.scheme == 'http' and parsed.hostname == '127.0.0.1') or
            (parsed.scheme == 'https' and (parsed.hostname or '').endswith('.ts.net'))):
        raise SafetyError('管理APIの宛先をloopback/正規Tailnet HTTPS以外にしません。')
    if parsed.username or parsed.password or not endpoint.startswith('/') or endpoint.startswith('//'):
        raise SafetyError('不正な管理API URLです。')
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    payload = None if body is None else json.dumps(body).encode('utf-8')
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
                Request(url, data=payload, headers=headers), timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024)
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = None
            return response.status, result, response.headers
    except HTTPError as exc:
        # Never print error bodies which may contain tokens or user data.
        return exc.code, None, exc.headers
    except (URLError, TimeoutError, OSError) as exc:
        raise SafetyError(f'接続できません: {parsed.hostname}:{parsed.port or 443}{endpoint}') from exc


def require_api(base: str, endpoint: str, body=None, token=None, timeout=15):
    code, data, _ = api(base, endpoint, body, token, timeout)
    if code != 200 or data is None:
        raise SafetyError(f'管理APIの確認に失敗しました: {endpoint} HTTP {code}')
    return data


def public_auth_guard(config: dict) -> None:
    if not isinstance(config, dict) or not isinstance(config.get('features'), dict):
        raise SafetyError('認証設定の応答形式が不明です。公開しません。')
    features = config['features']
    if config.get('onboarding') or features.get('auth') is not True:
        raise SafetyError('管理者未作成または認証無効です。公開しません。')
    if features.get('enable_signup') is not False or features.get('enable_login_form') is not True:
        raise SafetyError('登録停止・ログイン有効を確認できません。公開しません。')
    if features.get('auth_trusted_header'):
        raise SafetyError('信頼ヘッダー認証は今回の構成では使いません。')


def no_cloud_model(metadata: dict) -> None:
    if not isinstance(metadata, dict):
        raise SafetyError('モデル情報の形式が不明です。推論しません。')
    if metadata.get('remote_host') or metadata.get('remote_model'):
        raise SafetyError('クラウドモデルのメタデータを検出しました。推論しません。')


@contextmanager
def lock(path: Path, inheritable: bool = False):
    if path.is_symlink():
        raise SafetyError('ロックファイルがシンボリックリンクです。')
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        private(path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SafetyError('同じ処理またはWebUIが既に稼働しています。') from exc
        if inheritable:
            os.set_inheritable(fd, True)
        yield fd
    finally:
        os.close(fd)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def check_tree(path: Path) -> None:
    if path.is_symlink():
        raise SafetyError(f'バックアップ対象にシンボリックリンクがあります: {path.name}')
    for item in path.rglob('*'):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise SafetyError(f'通常ファイル以外を検出しました: {item.name}')


def tree_hashes(root: Path) -> dict[str, str]:
    check_tree(root)
    return {str(p.relative_to(root)): file_hash(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p != root / 'backup-manifest.json'}


def sqlite_check(path: Path) -> None:
    if not path.is_file():
        raise SafetyError('バックアップにwebui.dbがありません。')
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = connection.execute('PRAGMA quick_check').fetchall()
        if rows != [('ok',)]:
            raise SafetyError('SQLiteの整合性確認に失敗しました。')
    finally:
        connection.close()


def run_service(root: Path, service: str) -> None:
    """Thin launchd target; environment -> existing binary, not another server."""
    os.umask(0o077)
    private(root, directory=True)
    state = read_json(root / 'config' / 'state.json')
    env = read_env(root / 'config' / f'{service}.env')
    if service == 'ollama':
        if env != ollama_env():
            raise SafetyError('Ollamaの安全設定が変更されています。起動を停止します。')
        command = [state['ollama_bin'], 'serve']
    elif service == 'webui':
        if state['phase'] == 'https':
            assert_admin_database(root / 'data/webui.db')
        expected = web_env(root, state, env.get('WEBUI_SECRET_KEY', ''), state['phase'] == 'local')
        if env != expected or len(env.get('WEBUI_SECRET_KEY', '')) < 32:
            raise SafetyError('WebUIの環境設定が構成記録と一致しません。')
        deadline = time.monotonic() + 60
        while True:
            try:
                require_api(OLLAMA_URL, '/api/tags', timeout=2)
                break
            except SafetyError:
                if time.monotonic() >= deadline:
                    raise SafetyError('Ollamaの準備を60秒待ちました。起動元・ログを確認してください。')
                time.sleep(2)
        command = [str(root / 'venv/bin/open-webui'), 'serve', '--host', '127.0.0.1',
                   '--port', str(state['web_port'])]
    else:
        raise SafetyError('不正なサービス指定です。')
    os.chdir(root)
    with lock(root / 'records' / f'{service}.lock', inheritable=True):
        os.execve(command[0], command, clean_env(env))


def listener_records(output: str) -> dict[int, list[str]]:
    """Parse lsof -Fpn (machine-readable fields), no whitespace/name guessing."""
    result: dict[int, list[str]] = {}
    pid = None
    for line in output.splitlines():
        if line.startswith('p') and line[1:].isdigit():
            pid = int(line[1:])
            result.setdefault(pid, [])
        elif line.startswith('n') and pid is not None:
            result[pid].append(line[1:])
    return result


def assert_listener(records: dict[int, list[str]], port: int, pid: int) -> None:
    if set(records) != {pid} or not records[pid]:
        raise SafetyError('待受プロセスが専用LaunchAgentのPIDと一致しません。')
    if any(name != f'127.0.0.1:{port}' for name in records[pid]):
        raise SafetyError('待受がIPv4 loopbackのみに限定されていません。')


def assert_loopback_listener(records: dict[int, list[str]], port: int) -> None:
    """Accept any existing PID(s), but only IPv4 loopback on the exact port."""
    if not records:
        raise SafetyError(f'{port}番で既存サービスが待受していません。')
    names = [name for values in records.values() for name in values]
    if not names or any(name != f'127.0.0.1:{port}' for name in names):
        raise SafetyError(f'{port}番の既存サービスがIPv4 loopback限定ではありません。既存サービスを自動変更しません。')


def launch_plist(root: Path, service: str) -> dict:
    if service not in LABELS or not root.is_absolute():
        raise SafetyError('LaunchAgentの指定が不正です。')
    return {
        'Label': LABELS[service],
        'ProgramArguments': ['/bin/bash', str(root / 'scripts' / f'start-{service}.sh')],
        'WorkingDirectory': str(root), 'RunAtLoad': True,
        'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 10,
        'ExitTimeOut': 20, 'Umask': 0o077,
        'StandardOutPath': str(root / 'logs' / f'{service}.stdout.log'),
        'StandardErrorPath': str(root / 'logs' / f'{service}.stderr.log'),
    }


def create_snapshot(root: Path, destination: Path, version_string: str) -> Path:
    """Caller MUST stop WebUI, check port and hold its process lock beforehand."""
    import shutil
    if destination.exists() or destination.is_symlink():
        raise SafetyError('既存バックアップを上書きしません。')
    check_tree(root / 'data')
    sqlite_check(root / 'data' / 'webui.db')
    staging = destination.with_name(destination.name + '.incomplete')
    if staging.exists() or staging.is_symlink():
        raise SafetyError('同名の不完全バックアップがあります。調査してください。')
    staging.mkdir(mode=0o700)
    # Intentionally exclude venv/model weights/logs. The lock and exact version
    # are included. Runtime must be reconstructed on the same Mac architecture.
    for name in ('data', 'config', 'scripts', 'records'):
        check_tree(root / name)
        shutil.copytree(root / name, staging / name)
    for path in staging.rglob('*'):
        os.chmod(path, 0o700 if path.is_dir() else (0o700 if path.suffix == '.sh' else 0o600))
    sqlite_check(staging / 'data' / 'webui.db')
    manifest = {'project': PROJECT, 'format': 1, 'webui_version': version_string,
                'files': tree_hashes(staging)}
    write_json(staging / 'backup-manifest.json', manifest)
    staging.rename(destination)
    return destination


def verify_snapshot(path: Path) -> dict:
    private(path, directory=True)
    check_tree(path)
    manifest = read_json(path / 'backup-manifest.json')
    if manifest.get('project') != PROJECT or manifest.get('format') != 1:
        raise SafetyError('本パッケージのバックアップではありません。')
    if not isinstance(manifest.get('files'), dict) or manifest['files'] != tree_hashes(path):
        raise SafetyError('バックアップのハッシュが一致しません。復元しません。')
    sqlite_check(path / 'data' / 'webui.db')
    return manifest


def stage_snapshot(snapshot: Path, destination: Path, installed_version: str) -> None:
    import shutil
    manifest = verify_snapshot(snapshot)
    if manifest['webui_version'] != installed_version:
        raise SafetyError('アプリの版が一致しません。旧アプリと対応するデータを一組で復元してください。')
    if destination.exists() or destination.is_symlink():
        raise SafetyError('復元試験先は新しいフォルダでなければなりません。')
    destination.mkdir(mode=0o700)
    shutil.copytree(snapshot / 'data', destination / 'data')
    shutil.copytree(snapshot / 'config', destination / 'config')
    sqlite_check(destination / 'data' / 'webui.db')


def assert_backend_not_shared(config, port: int) -> None:
    """No pre-existing Serve handler may expose the initial admin bootstrap."""
    if isinstance(config, list):
        for item in config:
            assert_backend_not_shared(item, port)
    elif isinstance(config, dict):
        for key, val in config.items():
            if key in ('Proxy', 'TCPForward') and isinstance(val, str):
                parsed = urlparse(val if '://' in val else 'http://' + val)
                if parsed.port == port and parsed.hostname in ('127.0.0.1', 'localhost', '::1'):
                    raise SafetyError('既存Serve経路が新しいWebUIの待受へ転送しています。初期管理者を公開しないため停止します。')
            if isinstance(val, (dict, list)):
                assert_backend_not_shared(val, port)


def assert_admin_database(path: Path) -> None:
    """Read-only startup guard: a lost/empty DB must not reopen first-admin setup.

    The upstream `user.role` schema is explicitly checked; unknown schemas stop.
    No user identities, hashes or chat content are selected or returned.
    """
    if not path.is_file():
        raise SafetyError('HTTPS運用のDBがありません。初期管理者画面を再公開しないため起動しません。')
    private(path)
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        rows = connection.execute('SELECT COUNT(*), COALESCE(SUM(role = ?), 0) FROM "user"', ('admin',)).fetchone()
        if rows != (1, 1):
            raise SafetyError('本人用管理者1名のDBを確認できません。HTTPS運用を開始しません。')
    except sqlite3.Error as exc:
        raise SafetyError('DBの管理者構造を確認できません。DBを書き換えず調査してください。') from exc
    finally:
        if connection is not None:
            connection.close()
