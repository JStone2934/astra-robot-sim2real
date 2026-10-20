"""Zero-cost check of the Codex configuration for a Responses provider.

Creates the private Codex home exactly as a trial does, starts ``codex app-server``,
and performs ``initialize`` + ``thread/start`` only.  No model turn is created, so
this consumes no tokens; it verifies the provider block, the model catalog, and
the permission profile that a real trial will use.
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_env.agent import DEFAULT_CODEX, prepare_native
from sim_env.model_catalog import catalog_declares


def request(process, inbox, message, ident, timeout=20.):
    process.stdin.write(json.dumps(message)+'\n'); process.stdin.flush()
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        try:
            item = inbox.get(timeout=min(.5, max(.01, deadline-time.monotonic())))
        except queue.Empty:
            continue
        if item is None:
            raise RuntimeError('codex app-server exited before replying')
        if item.get('id') == ident and 'method' not in item:
            if 'error' in item:
                raise RuntimeError(item['error'].get('message', 'app-server request failed'))
            return item['result']
        if 'id' in item and 'method' in item:
            process.stdin.write(json.dumps({'id': item['id'], 'error': {
                'code': -32601, 'message': 'Read-only probe'}})+'\n')
            process.stdin.flush()
    raise TimeoutError('no reply to '+message.get('method', '?'))


def main():
    if not os.environ.get('ASTRA_BASE_URL'):
        print(json.dumps({'status': 'skipped', 'reason': 'ASTRA_BASE_URL is not set'}))
        return 0
    binary = os.environ.get('ASTRA_CODEX_BIN', DEFAULT_CODEX)
    if not Path(binary).is_file():
        print(json.dumps({'status': 'skipped', 'reason': f'codex binary not found: {binary}'}))
        return 0
    with tempfile.TemporaryDirectory(prefix='astra-deepseek-probe-') as folder:
        folder = Path(folder)
        workspace = folder/'workspace'; workspace.mkdir()
        private = folder/'private'; private.mkdir()
        home, config, env = prepare_native(workspace, folder/'unused.sock', private,
                                           reasoning=os.environ.get('ASTRA_EXPECTED_EFFORT', 'xhigh'))
        inbox = queue.Queue()
        stderr = (folder/'app-server.log').open('w')
        process = subprocess.Popen([binary, 'app-server'], cwd=workspace, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr, text=True)
        def reader():
            try:
                for line in process.stdout:
                    inbox.put(json.loads(line))
            except ValueError:
                pass
            finally:
                inbox.put(None)
        threading.Thread(target=reader, daemon=True).start()
        try:
            request(process, inbox, {'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'astra-deepseek-probe', 'version': '1.0'},
                'capabilities': {'experimentalApi': True}}}, 1)
            process.stdin.write(json.dumps({'method': 'initialized', 'params': {}})+'\n')
            process.stdin.flush()
            thread = request(process, inbox, {'id': 2, 'method': 'thread/start', 'params': {
                'cwd': str(workspace), 'model': config['model'], 'permissions': 'subject',
                'approvalPolicy': 'never', 'ephemeral': True}}, 2)['thread']
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            stderr.close()
        catalog = home/'models.json'
        result = {'status': 'ok', 'codex_binary': binary, 'model': config['model'],
                  'requested_effort': os.environ.get('ASTRA_EXPECTED_EFFORT', 'xhigh'),
                  'effective_effort': config['model_reasoning_effort'],
                  'model_provider': config.get('model_provider'),
                  'base_url': (config.get('model_providers') or {}).get(
                      config.get('model_provider'), {}).get('base_url'),
                  'model_catalog': str(catalog) if catalog.is_file() else None,
                  'catalog_declares_model': catalog_declares(catalog, config['model']) if catalog.is_file() else None,
                  'resolved_model': thread.get('model'),
                  'resolved_provider': thread.get('modelProvider')}
        ok = thread.get('model') == config['model']
        result['match'] = ok
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not ok:
            print('Model resolved differently: '+json.dumps(result), file=sys.stderr)
            return 1
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
