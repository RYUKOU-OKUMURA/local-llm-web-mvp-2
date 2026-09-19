#!/usr/bin/env python3
"""Personal native-macOS Open WebUI deployment CLI. No new web server.

Every mutation is local, explicitly invoked and limited to the package's own
files/jobs/new Serve route. Run `preflight` first; no root privileges required.
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import platform
import plistlib
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core import (
    PROJECT, PACKAGE_VERSION, OPENWEBUI_VERSION, ROOT, OLLAMA_URL, LABELS,
    SafetyError, version, model_tag, validate_ports, fqdn_from_status,
    private, atomic_write, write_json, read_json, write_env, read_env,
    policy, REQUIRED_KEYS, web_env, ollama_env, clean_env, extract_config_keys,
    updated_config, assert_config, normalized, port_references, expected_route,
    assert_owned_route, without_own_route, require_api, api, public_auth_guard,
    no_cloud_model, lock, run_service, listener_records, assert_listener, assert_loopback_listener,
    launch_plist, create_snapshot, verify_snapshot, stage_snapshot, assert_backend_not_shared,
)


def now() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def mac_only() -> None:
    if sys.platform != 'darwin':
        raise SafetyError('導入・起動操作はMac miniのmacOS上だけで実行してください。')
    if os.getuid() == 0:
        raise SafetyError('sudo / rootでは実行しません。普段のmacOSユーザーで実行してください。')


def command(args: list[str], *, capture=True, check=True, env=None, timeout=30):
    """No shell; never print arguments containing API credentials (none passed)."""
    try:
        result = subprocess.run(args, check=False, text=True,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None,
                                env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SafetyError(f'実行できませんでした: {Path(args[0]).name}') from exc
    if check and result.returncode:
        # Do not dump command output: it may contain provider/private metadata.
        raise SafetyError(f'{Path(args[0]).name} が失敗しました (終了コード {result.returncode})。端末/専用ログを確認してください。')
    return result


def locate(name: str, extra: tuple[str, ...] = ()) -> str:
    for path in [shutil.which(name), *extra]:
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return str(Path(path).absolute())
    raise SafetyError(f'{name}が見つかりません。READMEの事前準備を行ってください。')


def binaries() -> dict[str, str]:
    return {
        'uv_bin': locate('uv', (str(Path.home() / '.local/bin/uv'), '/opt/homebrew/bin/uv')),
        'ollama_bin': locate('ollama', ('/Applications/Ollama.app/Contents/Resources/ollama', '/opt/homebrew/bin/ollama')),
        'tailscale_bin': locate('tailscale', ('/Applications/Tailscale.app/Contents/MacOS/Tailscale', '/opt/homebrew/bin/tailscale')),
    }


def ts_json(state: dict, *args: str) -> dict:
    result = command([state['tailscale_bin'], *args, '--json'])
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SafetyError('TailscaleのJSON出力を解釈できません。既存設定は変更しません。') from exc
    if value is None and args == ('serve', 'status'):
        return {}
    if not isinstance(value, dict):
        raise SafetyError('Tailscaleの設定形式が想定外です。')
    return value


def listeners(port: int) -> dict[int, list[str]]:
    result = command(['/usr/sbin/lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fpn'], check=False)
    if result.returncode not in (0, 1):
        raise SafetyError('lsofで待受を確認できません。変更しません。')
    return listener_records(result.stdout)


def port_free(port: int) -> None:
    if listeners(port):
        raise SafetyError(f'{port}番は既に使用中です。既存プロセスを自動停止しません。')


# Ollama・OpenClawなど既存用途の番号は自動割当の候補から外す。
RESERVED_PORTS = frozenset({11434, 18789})


def pick_port(requested: int, *, avoid: set[int] = frozenset(), span: int = 100) -> int:
    """Return the requested port if free, else the nearest higher free port.

    Occupancy follows port_free (any-interface listener counts as busy).
    `avoid` holds not-yet-bound ports that must stay reserved for this run
    (e.g. the other new port's requested value)."""
    if not 1024 <= requested <= 65535:
        raise SafetyError('自動割当の起点は1024〜65535で指定してください。')
    blocked = set(avoid) | RESERVED_PORTS
    limit = min(requested + span, 65535)
    for candidate in range(requested, limit + 1):
        if candidate not in blocked and not listeners(candidate):
            return candidate
    raise SafetyError(f'{requested}〜{limit}番に空きポートがありません。別の番号を指定してください。')


def validate_existing_ollama() -> None:
    """Validate an Ollama server owned outside this package without mutating it."""
    assert_loopback_listener(listeners(11434), 11434)
    require_api(OLLAMA_URL, '/api/tags', timeout=5)


def managed_services(state: dict) -> tuple[str, ...]:
    return ('ollama', 'webui') if state.get('ollama_mode', 'managed') == 'managed' else ('webui',)


def load_state(installed=True) -> dict:
    private(ROOT, directory=True)
    for name in ('config', 'scripts', 'records', 'data', 'logs', 'backups'):
        private(ROOT / name, directory=True)
    state = read_json(ROOT / 'config/state.json')
    if state.get('project') != PROJECT or state.get('package_version') != PACKAGE_VERSION:
        raise SafetyError('本パッケージと一致する構成記録ではありません。自動変更しません。')
    if installed and not state.get('installed'):
        raise SafetyError('導入が完了していません。installを再実行してください。')
    version(state['webui_version'])
    validate_ports(state['web_port'], state['https_port'])
    model_tag(state['chat_model']); model_tag(state['embed_model'])
    if state.get('ollama_mode', 'managed') not in ('managed', 'existing'):
        raise SafetyError('不正なOllama管理モードです。')
    if state.get('phase') not in ('local', 'https'):
        raise SafetyError('不正な導入フェーズです。')
    return state


def save_state(state: dict) -> None:
    write_json(ROOT / 'config/state.json', state)


def confirm(message: str, *, yes=False) -> None:
    if not yes and input(message + ' [yes と入力]: ').strip() != 'yes':
        raise SafetyError('変更を中止しました。')


def preflight(args=None) -> dict:
    print('読み取り専用の確認です。設定・プロセスは変更しません。')
    if sys.platform != 'darwin':
        print(f'現在のOS: {platform.system()}。この環境ではmacOS導入を実行しません。')
        return {'platform': platform.system(), 'mac_ready': False}
    report = {'platform': platform.system(), 'architecture': platform.machine()}
    for key, args in (
        ('macOS', ['/usr/bin/sw_vers']),
        ('memory_bytes', ['/usr/sbin/sysctl', '-n', 'hw.memsize']),
        ('chip', ['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
    ):
        r = command(args, check=False)
        report[key] = r.stdout.strip() if r.returncode == 0 else '未確認'
    report['free_disk_gib'] = round(shutil.disk_usage(Path.home()).free / (1024 ** 3), 1)
    web_port = getattr(args, 'web_port', 3001) if args else 3001
    https_port = getattr(args, 'https_port', 9443) if args else 9443
    for port in (web_port, https_port, 11434, 18789):
        report[f'port_{port}'] = '使用中' if listeners(port) else '待受なし'
    for label, requested, avoid in (('web', web_port, {https_port}), ('https', https_port, {web_port})):
        if listeners(requested):
            try:
                report[f'{label}_port_auto_assign'] = pick_port(requested, avoid=avoid)
            except SafetyError:
                report[f'{label}_port_auto_assign'] = '空きなし'
    ollama_listeners = listeners(11434)
    report['ollama_listener_endpoints'] = sorted({name for values in ollama_listeners.values() for name in values})
    try:
        assert_loopback_listener(ollama_listeners, 11434)
        report['ollama_loopback_only'] = True
    except SafetyError:
        report['ollama_loopback_only'] = False
    try:
        bins = binaries()
        report['binaries'] = bins
        status = ts_json(bins, 'status')
        report['fqdn'] = fqdn_from_status(status)
        serve = ts_json(bins, 'serve', 'status')
        report[f'serve_{https_port}_in_use'] = port_references(serve, https_port)
        report['serve_443_in_use'] = port_references(serve, 443)
    except SafetyError as exc:
        report['preparation_needed'] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('Ollama.appのログイン項目、Homebrewサービス、Macのスリープ設定は別途確認してください。')
    return report


def inspect_package(state: dict) -> dict:
    py = str(ROOT / 'venv/bin/python')
    result = command([py, '-c', 'import importlib.metadata,json,sysconfig;print(json.dumps({"version":importlib.metadata.version("open-webui"),"site":sysconfig.get_paths()["purelib"]}))'])
    info = json.loads(result.stdout)
    if info['version'] != state['webui_version']:
        raise SafetyError('インストールされたOpen WebUIの版が固定値と一致しません。')
    package = Path(info['site']) / 'open_webui'
    source = (package / 'config.py').read_text(encoding='utf-8')
    mapping = extract_config_keys(source)
    missing = REQUIRED_KEYS - mapping.keys()
    if missing:
        raise SafetyError('採用版の安全設定キーを確認できません: ' + ', '.join(sorted(missing)) + '。APIやDBを推測して変更せず、ソースをレビューしてください。')
    write_json(ROOT / 'config/config-key-map.json', mapping)
    source += (package / 'env.py').read_text(encoding='utf-8')
    unsupported = sorted(key for key in policy(state) if key not in source)
    info['optional_settings_not_found'] = unsupported
    info['configuration_source_check'] = 'ASTでキーを確認。機能自体のE2E試験ではない。'
    write_json(ROOT / 'records/package-inspection.json', info)
    return info


def install(args) -> None:
    mac_only()
    bins = binaries()
    fqdn = fqdn_from_status(ts_json(bins, 'status'))
    validate_ports(args.web_port, args.https_port)
    version(args.webui_version); model_tag(args.chat_model); model_tag(args.embed_model)
    state = None
    if ROOT.exists():
        state = load_state(installed=False)
        if state['installed']:
            print('導入済みです。上書き更新しません。doctorで状態を確認してください。')
            return
    # 指定ポートが他ツールに使われていれば、近い空き番号へ一時割当する（既存側は変更しない）。
    web_port = pick_port(args.web_port, avoid={args.https_port})
    https_port = pick_port(args.https_port, avoid={web_port})
    if web_port != args.web_port or https_port != args.https_port:
        print(f'指定ポートは使用中です。今回の導入は WebUI={web_port}番 / HTTPS={https_port}番 を使います。')
    selected = {'web_port': web_port, 'https_port': https_port, 'webui_version': args.webui_version,
                'chat_model': args.chat_model, 'embed_model': args.embed_model, 'ollama_mode': args.ollama_mode}
    if state is not None:
        if (ROOT / 'data/webui.db').exists():
            raise SafetyError('未完了の導入に既存DBがあります。上書きせず調査してください。')
        for key, val in selected.items():
            if state[key] != val:
                raise SafetyError('途中導入の値と指定が違います。同じ指定で再試行してください。')
        if state['fqdn'] != fqdn:
            raise SafetyError('Tailscaleホスト名が変わっています。導入を再開しません。')
    else:
        state = dict(bins, project=PROJECT, package_version=PACKAGE_VERSION,
                     fqdn=fqdn, **selected, phase='local', installed=False,
                     published=False, created_at=now())
    if state['ollama_mode'] == 'managed':
        port_free(11434)
    else:
        validate_existing_ollama()
    port_free(state['web_port']); port_free(state['https_port'])
    existing_serve = ts_json(state, 'serve', 'status')
    assert_backend_not_shared(existing_serve, state['web_port'])
    if port_references(existing_serve, state['https_port']):
        raise SafetyError('WebUI用のServeポートは既に使用中です。既存経路を変更しません。')
    for service in managed_services(state):
        label = LABELS[service]
        target = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
        if target.exists() or target.is_symlink() or job_pid(service) is not None:
            raise SafetyError(f'同名のLaunchAgentが存在します: {label}。所有権を推測しません。')
    confirm('専用フォルダへPython/Open WebUIと依存物を取得します。既存Ollama/Tailscale本体は更新しません。', yes=args.yes)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    private(ROOT, directory=True)
    for name in ('config', 'data', 'scripts', 'logs', 'records', 'backups'):
        (ROOT / name).mkdir(mode=0o700, exist_ok=True)
        private(ROOT / name, directory=True)
    with lock(ROOT / 'records/operation.lock'):
        save_state(state)
        if not (ROOT / 'config/webui.env').exists():
            write_env(ROOT / 'config/webui.env', web_env(ROOT, state, secrets.token_hex(32), True))
        if state['ollama_mode'] == 'managed':
            write_env(ROOT / 'config/ollama.env', ollama_env())
        uv = state['uv_bin']
        # Resolve dependencies on the actual Mac; never ship a Linux lock as a Mac lock.
        command([uv, 'python', 'install', '3.11'], capture=False, timeout=None)
        if not (ROOT / 'venv').exists():
            command([uv, 'venv', '--python', '3.11', str(ROOT / 'venv')], capture=False, timeout=None)
        py = str(ROOT / 'venv/bin/python')
        atomic_write(ROOT / 'config/requirements.in', f'open-webui=={state["webui_version"]}\n')
        command([uv, 'pip', 'compile', '--python', py, '--generate-hashes', str(ROOT / 'config/requirements.in'), '--output-file', str(ROOT / 'config/requirements.lock')], capture=False, timeout=None)
        command([uv, 'pip', 'sync', '--python', py, '--require-hashes', str(ROOT / 'config/requirements.lock')], capture=False, timeout=None)
        command([uv, 'pip', 'check', '--python', py], capture=False, timeout=None)
        inspect_package(state)
        # Prewarm tokenizer on synthetic/no user content. Normal services are offline-mode.
        cache = ROOT / 'data/cache/tiktoken'
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        command([py, '-c', "import tiktoken;tiktoken.get_encoding('cl100k_base')"],
                capture=False, timeout=None, env=clean_env({'TIKTOKEN_CACHE_DIR': str(cache)}))
        source_dir = Path(__file__).resolve().parent
        for name in ('core.py', 'llmweb.py'):
            atomic_write(ROOT / 'scripts' / name, (source_dir / name).read_text(encoding='utf-8'))
        for service in managed_services(state):
            wrapper = '#!/bin/bash\nset -euo pipefail\nexec ' + ' '.join(shlex.quote(x) for x in [py, str(ROOT / 'scripts/llmweb.py'), '_run', service]) + '\n'
            atomic_write(ROOT / 'scripts' / f'start-{service}.sh', wrapper, 0o700)
            content = plistlib.dumps(launch_plist(ROOT, service)).decode('utf-8')
            atomic_write(ROOT / 'config' / (LABELS[service] + '.plist'), content)
        state['installed'] = True
        save_state(state)
        versions = {'installed_at': now(), 'webui_version': state['webui_version'],
                    'python': command([py, '--version']).stdout.strip(),
                    'uv': command([uv, '--version']).stdout.strip(),
                    'ollama': command([state['ollama_bin'], '--version'], check=False).stdout.strip(),
                    'macOS': command(['/usr/bin/sw_vers']).stdout.strip(),
                    'machine': platform.machine(), 'binaries': bins, 'ollama_mode': state['ollama_mode']}
        write_json(ROOT / 'records/versions.json', versions)
    print('専用環境の導入処理が完了しました。まだサービス起動・管理者作成・遠隔公開はしていません。')


def domain(service: str) -> str:
    return f'gui/{os.getuid()}/{LABELS[service]}'


def job_info(service: str):
    return command(['/bin/launchctl', 'print', domain(service)], check=False)


def job_pid(service: str) -> int | None:
    result = job_info(service)
    if result.returncode:
        return None
    match = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.M)
    return int(match[1]) if match else 0


def ensure_plist(service: str, create=False) -> Path:
    path = Path.home() / 'Library/LaunchAgents' / (LABELS[service] + '.plist')
    expected = launch_plist(ROOT, service)
    if path.exists() or path.is_symlink():
        private(path)
        if plistlib.loads(path.read_bytes()) != expected:
            raise SafetyError('既存LaunchAgentの内容が本パッケージと一致しません。変更しません。')
    elif create:
        path.parent.mkdir(exist_ok=True)
        atomic_write(path, plistlib.dumps(expected).decode('utf-8'))
    else:
        raise SafetyError('専用LaunchAgentの所有を確認できません。停止操作をしません。')
    return path


def ensure_running(state: dict, service: str) -> None:
    if service == 'ollama' and state.get('ollama_mode', 'managed') == 'existing':
        validate_existing_ollama()
        return
    pid = job_pid(service)
    if not pid:
        raise SafetyError(f'{service}の専用LaunchAgentが稼働していません。')
    ensure_plist(service)
    assert_listener(listeners(11434 if service == 'ollama' else state['web_port']),
                    11434 if service == 'ollama' else state['web_port'], pid)


def start_service(state: dict, service: str) -> None:
    if service == 'ollama' and state.get('ollama_mode', 'managed') == 'existing':
        validate_existing_ollama()
        print('ollama: 既存プロセスを再利用します（本パッケージでは起動・停止しません）。')
        return
    if service == 'webui' and state['phase'] == 'local':
        assert_backend_not_shared(ts_json(state, 'serve', 'status'), state['web_port'])
    pid = job_pid(service)
    if pid:
        ensure_running(state, service)
        print(f'{service}: 専用プロセスが既に稼働中です。')
        return
    port_free(11434 if service == 'ollama' else state['web_port'])
    path = ensure_plist(service, create=True)
    if pid is None:
        command(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)])
    else:
        command(['/bin/launchctl', 'kickstart', domain(service)])
    base = OLLAMA_URL if service == 'ollama' else f'http://127.0.0.1:{state["web_port"]}'
    endpoint = '/api/tags' if service == 'ollama' else '/api/config'
    deadline = time.monotonic() + (60 if service == 'ollama' else 180)
    while time.monotonic() < deadline:
        try:
            require_api(base, endpoint, timeout=3)
            ensure_running(state, service)
            print(f'{service}: 起動を確認しました。')
            return
        except SafetyError:
            time.sleep(2)
    raise SafetyError(f'{service}の起動を確認できませんでした。logs内を確認し、連続失敗時はstopしてください。')


def stop_service(state: dict, service: str) -> bool:
    if service == 'ollama' and state.get('ollama_mode', 'managed') == 'existing':
        raise SafetyError('既存Ollamaは本パッケージの所有物ではないため停止しません。')
    pid = job_pid(service)
    if pid is None:
        return False
    ensure_plist(service)
    if pid:
        # A failed server may not listen; never signal unrelated port owners.
        current = listeners(11434 if service == 'ollama' else state['web_port'])
        if current and set(current) != {pid}:
            raise SafetyError('待受の所有者が異なります。停止せず調査してください。')
    command(['/bin/launchctl', 'bootout', domain(service)])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if job_pid(service) is None and not listeners(11434 if service == 'ollama' else state['web_port']):
            return True
        time.sleep(1)
    raise SafetyError('専用ジョブの停止を確認できません。バックアップや再起動は中止します。')


def start(args) -> None:
    mac_only(); state = load_state()
    with lock(ROOT / 'records/operation.lock'):
        for service in (('ollama', 'webui') if args.service == 'all' else (args.service,)):
            start_service(state, service)
    print('通常URL: ' + (f'http://127.0.0.1:{state["web_port"]}' if state['phase'] == 'local' else policy(state)['WEBUI_URL']))


def stop(args) -> None:
    mac_only(); state = load_state()
    with lock(ROOT / 'records/operation.lock'):
        services = tuple(reversed(managed_services(state))) if args.service == 'all' else (args.service,)
        for service in services:
            stopped = stop_service(state, service)
            print(f'{service}: ' + ('停止しました。' if stopped else 'ジョブは未登録です。'))
    print('LaunchAgent定義は残るため、次回ログインでは再開します。自動起動停止はOPERATIONS.md参照。')


def models_ready(state: dict) -> dict:
    catalog = require_api(OLLAMA_URL, '/api/tags')
    indexed = {m.get('name'): m for m in catalog.get('models', [])}
    for tag in (state['chat_model'], state['embed_model']):
        if tag not in indexed:
            raise SafetyError(f'モデル未取得: {tag}。pull-modelsを実行してください。')
        no_cloud_model(require_api(OLLAMA_URL, '/api/show', {'model': tag}, timeout=60))
    selected = {tag: indexed[tag] for tag in (state['chat_model'], state['embed_model'])}
    write_json(ROOT / 'records/models.json', selected)
    return selected


def pull_models(args) -> None:
    mac_only(); state = load_state()
    with lock(ROOT / 'records/operation.lock'):
        start_service(state, 'ollama')
        confirm(f'モデル {state["chat_model"]} と {state["embed_model"]} を取得します。ネット通信とディスク容量を使います。', yes=args.yes)
        for tag in (state['chat_model'], state['embed_model']):
            command([state['ollama_bin'], 'pull', tag], capture=False, timeout=None, env=clean_env(ollama_env()))
        models_ready(state)
    print('ローカルモデルを取得・識別記録しました。速度と日本語文書の品質は未評価です。')


def smoke(_args=None) -> None:
    mac_only(); state = load_state(); ensure_running(state, 'ollama'); models_ready(state)
    start_time = time.monotonic()
    reply = require_api(OLLAMA_URL, '/api/chat', {
        'model': state['chat_model'], 'messages': [{'role': 'user', 'content': '日本語で「接続テストに成功しました」とだけ返してください。'}],
        'stream': False, 'think': False, 'options': {'num_ctx': 4096, 'num_predict': 128}}, timeout=600)
    text = reply.get('message', {}).get('content', '')
    if not isinstance(text, str) or not text.strip():
        raise SafetyError('チャットの本文が空です。モデル出力を確認してください。')
    elapsed = round(time.monotonic() - start_time, 2)
    embed = require_api(OLLAMA_URL, '/api/embed', {'model': state['embed_model'], 'input': '架空の資料。青葉の管理番号はAOBA-731。'}, timeout=600)
    vectors = embed.get('embeddings', [])
    if not vectors or not vectors[0] or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vectors[0]):
        raise SafetyError('ローカル埋め込みの数値を確認できませんでした。')
    result = {'at': now(), 'test': 'Ollama直接API・架空文書のみ', 'chat_seconds': elapsed,
              'chat_response': text, 'embedding_dimensions': len(vectors[0]),
              'NOT_tested': 'WebUIチャット/ストリーム/文書検索/遠隔通信/性能合格'}
    write_json(ROOT / 'records/smoke.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def admin_token(base: str) -> str:
    email = input('WebUI管理者メール（保存しません）: ').strip()
    password = getpass.getpass('WebUIパスワード（非表示・保存しません）: ')
    response = require_api(base, '/api/v1/auths/signin', {'email': email, 'password': password})
    del password
    if response.get('role') != 'admin' or not isinstance(response.get('token'), str):
        raise SafetyError('管理者認証を確認できません。')
    return response['token']


def expected_config(state: dict) -> dict:
    mapping = read_json(ROOT / 'config/config-key-map.json')
    if REQUIRED_KEYS - mapping.keys():
        raise SafetyError('必須の安全設定キーが不足しています。公開しません。')
    return {mapping[k]: value for k, value in policy(state).items() if k in mapping}


def verify_admin_config(state: dict, token: str, base: str) -> dict:
    exported = require_api(base, '/api/v1/configs/export', token=token)
    if not isinstance(exported, dict):
        raise SafetyError('管理設定の出力形式が不明です。')
    assert_config(exported, expected_config(state))
    return exported


def secure(_args=None) -> None:
    mac_only(); state = load_state()
    if state.get('published') or state.get('publication_pending'):
        raise SafetyError('公開中の設定変更はしません。先にunpublishしてください。')
    with lock(ROOT / 'records/operation.lock'):
        ensure_running(state, 'webui'); ensure_running(state, 'ollama'); models_ready(state)
        if port_references(ts_json(state, 'serve', 'status'), state['https_port']):
            raise SafetyError('初期設定前に対象ポートが既に共有されています。自動変更しません。')
        base = f'http://127.0.0.1:{state["web_port"]}'
        token = admin_token(base)
        before = require_api(base, '/api/v1/configs/export', token=token)
        if not isinstance(before, dict):
            raise SafetyError('保存設定の形式が想定外です。')
        write_json(ROOT / 'records' / ('webui-config-before-' + now() + '.json'), before)
        # Preserve unrelated settings; only modify supported safety keys via official API.
        after = updated_config(before, expected_config(state))
        require_api(base, '/api/v1/configs/import', {'config': after}, token=token)
        verify_admin_config(state, token, base)
        del token
        stop_service(state, 'webui')
        env = read_env(ROOT / 'config/webui.env')
        state['phase'] = 'https'
        write_env(ROOT / 'config/webui.env', web_env(ROOT, state, env['WEBUI_SECRET_KEY'], False))
        save_state(state)
        start_service(state, 'webui')
        public_auth_guard(require_api(base, '/api/config'))
    print('登録停止・ローカル接続・HTTPS用設定を反映しました。まだ遠隔公開していません。')


def assert_final_env(state: dict) -> None:
    if state['phase'] != 'https':
        raise SafetyError('先にローカル管理者作成とsecureを完了してください。')
    env = read_env(ROOT / 'config/webui.env')
    if env != web_env(ROOT, state, env.get('WEBUI_SECRET_KEY', ''), False):
        raise SafetyError('環境ファイルが基準値から変更されています。公開しません。')


def publish(args) -> None:
    mac_only(); state = load_state()
    with lock(ROOT / 'records/operation.lock'):
        assert_final_env(state)
        ensure_running(state, 'ollama'); ensure_running(state, 'webui'); models_ready(state)
        if fqdn_from_status(ts_json(state, 'status')) != state['fqdn']:
            raise SafetyError('ホスト名が変わっています。公開しません。')
        base = f'http://127.0.0.1:{state["web_port"]}'
        public_auth_guard(require_api(base, '/api/config'))
        token = admin_token(base)
        verify_admin_config(state, token, base)
        del token
        before = ts_json(state, 'serve', 'status')
        if state.get('published'):
            assert_owned_route(before, state)
            print('既に専用経路で共有中です: ' + policy(state)['WEBUI_URL'])
            return
        if port_references(before, state['https_port']):
            raise SafetyError('対象ポートは使用中です。unpublishまたは現状確認を先に行ってください。')
        port_free(state['https_port'])
        confirm('Tailnet内の到達権限を確認し、本人端末からだけ利用する運用にしましたか？（本ツールはACLを変更しません）', yes=args.yes)
        stamp = now()
        write_json(ROOT / 'records' / ('serve-before-' + stamp + '.json'), before)
        state['publication_pending'] = True
        save_state(state)
        command([state['tailscale_bin'], 'serve', '--bg', f'--https={state["https_port"]}', f'http://127.0.0.1:{state["web_port"]}'])
        after = ts_json(state, 'serve', 'status')
        write_json(ROOT / 'records' / ('serve-after-' + stamp + '.json'), after)
        try:
            assert_owned_route(after, state)
            if without_own_route(after, state) != normalized(before):
                raise SafetyError('既存Serve設定の変化を検出しました。全体を巻き戻さず停止します。')
            public_auth_guard(require_api(policy(state)['WEBUI_URL'], '/api/config'))
        except SafetyError:
            # Do not restore whole Serve config; another process may own changed fields.
            stop_service(state, 'webui')
            print('検証失敗のため新規WebUIを停止しました。Serve全体は変更せず残しています。unpublishで専用経路だけを外せます。')
            raise
        state['publication_pending'] = False
        state['published'] = True
        state['published_at'] = stamp
        save_state(state)
    print('専用HTTPS経路の追加と既存設定の保持を確認しました: ' + policy(state)['WEBUI_URL'])
    print('MacBookの別回線、OpenClawの実会話、Cookie干渉はACCEPTANCE.mdで実機確認してください。')


def unpublish(_args=None) -> None:
    mac_only(); state = load_state()
    with lock(ROOT / 'records/operation.lock'):
        if not state.get('published') and not state.get('publication_pending'):
            raise SafetyError('このパッケージが追加したという記録がありません。Serveを変更しません。')
        before = ts_json(state, 'serve', 'status')
        if not port_references(before, state['https_port']):
            state['published'] = False; state['publication_pending'] = False; save_state(state)
            print('専用経路は既にありません。記録だけを修正しました。'); return
        assert_owned_route(before, state)
        command([state['tailscale_bin'], 'serve', '--bg', f'--https={state["https_port"]}', 'off'])
        after = ts_json(state, 'serve', 'status')
        if normalized(after) != without_own_route(before, state):
            raise SafetyError('削除後のServe設定が想定と異なります。他経路は自動変更しません。')
        state['published'] = False; state['publication_pending'] = False; save_state(state)
    print('WebUI専用のHTTPS経路だけを削除しました。443番・OpenClaw・会話データは残しています。')


def doctor(_args=None) -> None:
    mac_only(); state = load_state()
    errors = []
    for service in ('ollama', 'webui'):
        try:
            ensure_running(state, service); print(f'OK {service}: ' + ('既存プロセス / IPv4 loopback' if service == 'ollama' and state.get('ollama_mode') == 'existing' else '専用PID / IPv4 loopback'))
        except SafetyError as exc:
            errors.append(str(exc)); print('NG ' + str(exc))
    base = f'http://127.0.0.1:{state["web_port"]}'
    try:
        config = require_api(base, '/api/config')
        if config.get('version') != state['webui_version']:
            raise SafetyError('稼働WebUIのバージョンを固定値と照合できません。')
        if state['phase'] == 'https':
            public_auth_guard(config); assert_final_env(state)
        code, _, _ = api(base, '/api/v1/chats/')
        if code not in (401, 403):
            raise SafetyError('未認証の会話APIが401/403になりません。')
        print('OK WebUI: バージョン・未認証の会話API拒否')
        if state.get('published'):
            assert_owned_route(ts_json(state, 'serve', 'status'), state)
            public_auth_guard(require_api(policy(state)['WEBUI_URL'], '/api/config'))
            print('OK Serve: 専用経路 / HTTPS到達')
    except SafetyError as exc:
        errors.append(str(exc)); print('NG ' + str(exc))
    print('診断は部分的です。管理画面の保存設定、通信先、文書検索、OpenClaw共存は別途検証が必要です。')
    if errors:
        raise SafetyError(f'{len(errors)}件の未解決事項があります。公開範囲を広げて解決しないでください。')


def backup(_args=None) -> None:
    mac_only(); state = load_state()
    with lock(ROOT / 'records/operation.lock'):
        was_loaded = job_pid('webui') is not None
        stop_service(state, 'webui')
        port_free(state['web_port'])
        try:
            with lock(ROOT / 'records/webui.lock'):
                destination = ROOT / 'backups' / now()
                create_snapshot(ROOT, destination, state['webui_version'])
                verify_snapshot(destination)
                print('停止中バックアップを作成しました: ' + str(destination))
                print('このバックアップは秘密鍵・会話・添付を含む未暗号化データです。他人へ渡さないでください。')
        finally:
            if was_loaded:
                start_service(state, 'webui')
    print('同じMac内の保存は本体故障対策にはなりません。別媒体への保管は手動で行ってください。')


def restore_test(args) -> None:
    mac_only(); state = load_state()
    snapshot = Path(args.snapshot).expanduser().absolute()
    if snapshot.parent != ROOT / 'backups':
        raise SafetyError('本プロジェクトのbackups直下だけを復元試験の入力にします。')
    port = args.port
    validate_ports(port, state['https_port'])
    if port == state['web_port']:
        raise SafetyError('本番WebUIのポートを復元試験に使いません。')
    port = pick_port(port, avoid={state['web_port'], state['https_port']})
    if port != args.port:
        print(f'{args.port}番は使用中です。復元試験は一時的に{port}番を使います。')
    with lock(ROOT / 'records/operation.lock'):
        port_free(port); ensure_running(state, 'ollama')
        confirm('停止・復元の影響を分けるため、本番WebUIを停止し、隔離コピーをHTTPで開きます。Ctrl+Cで試験を終了します。', yes=args.yes)
        was_loaded = stop_service(state, 'webui')
        parent = ROOT / 'restore-tests'
        parent.mkdir(mode=0o700, exist_ok=True); private(parent, directory=True)
        destination = parent / now()
        try:
            stage_snapshot(snapshot, destination, state['webui_version'])
            saved = read_env(destination / 'config/webui.env')
            test_state = dict(state, web_port=port)
            env = web_env(ROOT, test_state, saved['WEBUI_SECRET_KEY'], True)
            env.update(DATA_DIR=str(destination / 'data'), ENABLE_SIGNUP='false',
                       ENABLE_PERSISTENT_CONFIG='false',
                       TIKTOKEN_CACHE_DIR=str(destination / 'data/cache/tiktoken'))
            print(f'隔離復元: http://127.0.0.1:{port} （Mac miniのブラウザのみ）')
            print('過去の会話・添付・参照元を手動確認してください。SQLite整合性だけではAT-18合格にはしません。')
            subprocess.run([str(ROOT / 'venv/bin/open-webui'), 'serve', '--host', '127.0.0.1', '--port', str(port)],
                           env=clean_env(env), cwd=destination, check=False)
        finally:
            if was_loaded:
                start_service(state, 'webui')
    print('本番データは置換していません。隔離コピーは残しています。')


def build_parser():
    parser = argparse.ArgumentParser(description='Ollama + Open WebUI 個人用macOS導入・運用（既存OpenClawは変更しません）')
    parser.add_argument('--version', action='version', version=PACKAGE_VERSION)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('preflight', help='読み取り専用で実機の前提を確認')
    p.add_argument('--web-port', type=int, default=3001)
    p.add_argument('--https-port', type=int, default=9443)
    p = sub.add_parser('install', help='専用venv・設定・起動スクリプトを配置。サービスは未起動')
    p.add_argument('--webui-version', default=OPENWEBUI_VERSION)
    p.add_argument('--web-port', type=int, default=3001)
    p.add_argument('--https-port', type=int, default=9443)
    p.add_argument('--chat-model', default='qwen3:4b')
    p.add_argument('--embed-model', default='bge-m3:latest')
    p.add_argument('--ollama-mode', choices=('managed', 'existing'), default='managed', help='existingは既に安全に起動中のOllamaを再利用し、本ツールでは停止しません')
    p.add_argument('--yes', action='store_true')
    for name in ('start', 'stop'):
        p = sub.add_parser(name)
        p.add_argument('service', choices=('ollama', 'webui', 'all'), nargs='?', default='all')
    p = sub.add_parser('pull-models', help='設定したモデルを明示的に取得'); p.add_argument('--yes', action='store_true')
    sub.add_parser('smoke', help='Ollamaへの直接APIテスト。WebUIのE2Eではない')
    sub.add_parser('secure', help='管理者認証・保存設定の制限・HTTPSモードへ変更')
    p = sub.add_parser('publish', help='安全設定を照合して専用Serve経路を追加'); p.add_argument('--yes', action='store_true')
    sub.add_parser('unpublish', help='記録と完全一致する専用Serve経路だけを削除')
    sub.add_parser('doctor', help='起動・待受・公開の部分診断')
    sub.add_parser('backup', help='WebUI停止中に全DATA_DIRと設定を保存')
    p = sub.add_parser('restore-test', help='本番を上書きしない隔離コピーの復元試験')
    p.add_argument('snapshot'); p.add_argument('--port', type=int, default=3010); p.add_argument('--yes', action='store_true')
    p = sub.add_parser('_run', help='内部用：LaunchAgentの実行先。手動実行しない'); p.add_argument('service', choices=tuple(LABELS))
    return parser


def main() -> int:
    os.umask(0o077)
    args = build_parser().parse_args()
    try:
        if args.action == 'preflight': preflight(args)
        elif args.action == '_run': mac_only(); run_service(ROOT, args.service)
        else:
            functions = {'install': install, 'start': start, 'stop': stop, 'pull-models': pull_models,
                         'smoke': smoke, 'secure': secure, 'publish': publish, 'unpublish': unpublish,
                         'doctor': doctor, 'backup': backup, 'restore-test': restore_test}
            functions[args.action](args)
        return 0
    except (SafetyError, OSError, ValueError, KeyError) as exc:
        # Our messages do not contain passwords/tokens; do not print full tracebacks.
        print('停止: ' + str(exc), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\n中断しました。状態はdoctorとlogsで確認してください。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
