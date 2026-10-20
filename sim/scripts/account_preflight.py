"""Read the launcher's account, quota and model catalog; no thread or model turn."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_env.agent import DEFAULT_CODEX, prepare_native
from sim_env.model_catalog import catalog_declares, effective_reasoning


def provider_preflight(expected_model, expected_effort, *, inference=True):
    """Validate a Responses-provider block without touching the ChatGPT account.

    Checks the credential, the DeepSeek model catalog, and (optionally) one
    minimal inference call.  The API key is never written to disk or output.
    """
    base = os.environ['ASTRA_BASE_URL'].rstrip('/')
    token = os.environ.get('ASTRA_API_KEY')
    model = os.environ.get('ASTRA_MODEL')
    if not token or not model:
        raise RuntimeError('ASTRA_BASE_URL requires ASTRA_API_KEY and ASTRA_MODEL')
    if expected_model and model != expected_model:
        raise RuntimeError(f'Configured model {model!r} differs from expected {expected_model!r}')
    requested_effort = expected_effort or os.environ.get('ASTRA_EXPECTED_EFFORT', 'xhigh')
    effort = effective_reasoning(model, requested_effort)
    source_home = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
    catalog_path = Path(os.environ.get('ASTRA_MODELS_JSON') or source_home/'models.json')
    catalog = {'path': str(catalog_path), 'present': catalog_path.is_file(),
               'declares_model': catalog_declares(catalog_path, model)}
    if not catalog['present']:
        raise RuntimeError('Model catalog missing; set ASTRA_MODELS_JSON to sim/scripts/deepseek-models.json')
    if not catalog['declares_model']:
        raise RuntimeError(f'Model catalog does not declare {model!r}')
    version = None
    try:
        version = subprocess.check_output([os.environ.get('ASTRA_CODEX_BIN', DEFAULT_CODEX), '--version'],
                                          text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        version = None
    result = {'checked_unix': time.time(), 'mode': 'provider', 'endpoint': base, 'model': model,
              'tested_model': model, 'effort': effort, 'requested_effort': requested_effort,
              'catalog': catalog, 'codex_version': version, 'model_turns_started': 0}
    if not inference:
        result['inference'] = 'skipped'
        return result
    payload = {'model': model, 'input': 'Reply with OK.', 'max_output_tokens': 16,
               'reasoning': {'effort': effort}, 'stream': False}
    request = urllib.request.Request(base+'/responses', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+token}, method='POST')
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode('utf-8', 'replace')
        raise RuntimeError(f'Provider HTTP {exc.code}: {detail}') from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Provider transport unavailable: {exc.reason}') from None
    result.update({'inference': 'ok', 'latency_seconds': round(time.monotonic()-started, 3),
                   'output_text': (body.get('output_text') or '')[:80],
                   'usage': body.get('usage'), 'model_turns_started': 1})
    return result


def preflight(expected_model=None, expected_effort=None, *, inference=True):
    expected_model = expected_model or os.environ.get('ASTRA_EXPECTED_MODEL', 'gpt-6-astra')
    expected_effort = expected_effort or os.environ.get('ASTRA_EXPECTED_EFFORT', 'xhigh')
    if os.environ.get('ASTRA_BASE_URL'):
        return provider_preflight(expected_model, expected_effort, inference=inference)
    with tempfile.TemporaryDirectory(prefix='astra-account-preflight-') as folder:
        folder = Path(folder)
        workspace = folder/'workspace'; workspace.mkdir()
        private = folder/'private'; private.mkdir()
        home, config, env = prepare_native(workspace, folder/'unused.sock', private,
                                           reasoning=expected_effort)
        if config['model'] != expected_model or config.get('model_provider') not in (None, 'openai'):
            raise RuntimeError('Launcher model/provider does not match frozen experiment')
        auth = json.loads((home/'auth.json').read_text())
        account_id = auth.get('tokens', {}).get('account_id')
        if not account_id:
            raise RuntimeError('No ChatGPT account identity in launcher credentials')
        fingerprint = hashlib.sha256(account_id.encode()).hexdigest()
        del auth, account_id
        inbox = queue.Queue()
        process = subprocess.Popen([os.environ.get('ASTRA_CODEX_BIN', DEFAULT_CODEX), 'app-server'],
                                   cwd=workspace, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        def reader():
            try:
                for line in process.stdout:
                    inbox.put(json.loads(line))
            finally:
                inbox.put(None)
        threading.Thread(target=reader, daemon=True).start()
        sequence = 0
        def send(value):
            process.stdin.write(json.dumps(value)+'\n'); process.stdin.flush()
        def request(method, params):
            nonlocal sequence
            sequence += 1
            send({'id': sequence, 'method': method, 'params': params})
            deadline = time.monotonic()+45
            while time.monotonic() < deadline:
                try: item = inbox.get(timeout=min(1, max(.01, deadline-time.monotonic())))
                except queue.Empty: continue
                if item is None:
                    raise RuntimeError('Account preflight app-server exited')
                if item.get('id') == sequence and 'method' not in item:
                    if 'error' in item:
                        raise RuntimeError(f'{method}: {item["error"].get("message", "request failed")}')
                    return item['result']
                if 'id' in item and 'method' in item:
                    send({'id': item['id'], 'error': {'code': -32601, 'message': 'Read-only preflight'}})
            raise TimeoutError('Account preflight timed out: '+method)
        try:
            request('initialize', {'clientInfo': {'name': 'astra-quota-preflight', 'version': '1.0'},
                                   'capabilities': {'experimentalApi': True}})
            send({'method': 'initialized', 'params': {}})
            account = request('account/read', {'refreshToken': False}).get('account') or {}
            if account.get('type') != 'chatgpt':
                raise RuntimeError('Launcher did not authenticate with ChatGPT')
            limits = request('account/rateLimits/read', {})
            bucket = (limits.get('rateLimitsByLimitId') or {}).get('codex') or limits.get('rateLimits')
            if not bucket:
                raise RuntimeError('No Codex quota returned')
            model = None; cursor = None
            for _ in range(10):
                params = {'limit': 100}
                if cursor: params['cursor'] = cursor
                catalog = request('model/list', params)
                model = next((x for x in catalog['data'] if x.get('model') == expected_model), None)
                if model: break
                cursor = catalog.get('nextCursor')
                if not cursor: break
            efforts = [x.get('reasoningEffort') for x in (model or {}).get('supportedReasoningEfforts', [])]
            if not model or expected_effort not in efforts:
                raise RuntimeError('Requested model/effort is absent from account model catalog')
            windows = []
            for key in ('primary', 'secondary'):
                w = bucket.get(key)
                if w and isinstance(w.get('usedPercent'), (int, float)):
                    windows.append({'name': key, **w, 'remaining_percent': max(0, 100-w['usedPercent'])})
            if not windows:
                raise RuntimeError('No measurable account quota window')
            return {'checked_unix': time.time(), 'account_fingerprint': fingerprint,
                    'plan': bucket.get('planType') or account.get('planType'), 'model': expected_model,
                    'effort': expected_effort, 'catalog_available': True, 'windows': windows,
                    'minimum_remaining_percent': min(w['remaining_percent'] for w in windows),
                    'spend_control_reached': bucket.get('spendControlReached'),
                    'rate_limit_reached_type': bucket.get('rateLimitReachedType'),
                    'reset_credits_consumed': False, 'model_turns_started': 0}
        finally:
            process.stdin.close()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            process.stdout.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check the model account/provider before a trial block.')
    parser.add_argument('--model', help='expected model id (default: ASTRA_EXPECTED_MODEL or gpt-6-astra)')
    parser.add_argument('--effort', help='expected effort (default: ASTRA_EXPECTED_EFFORT or xhigh)')
    parser.add_argument('--no-inference', action='store_true',
                        help='validate credentials/catalog only; do not call the model')
    args = parser.parse_args()
    print(json.dumps(preflight(args.model, args.effort, inference=not args.no_inference),
                     ensure_ascii=False, indent=2))
