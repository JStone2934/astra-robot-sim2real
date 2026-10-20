"""Zero-cost check of the DeepSeek Harness (DSH) runner setup.

Resolves the ``dsh`` binary, initializes a throwaway DSH home, and composes the
headless profile with the trial overlay.  No model turn is created, so this
consumes no tokens; it verifies the CLI, profile initialization, and that the
overlay selects the expected model and reasoning level.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim_env.agent_dsh import DEFAULT_MODEL, DEFAULT_PROVIDER, write_overlay
from sim_env.model_catalog import effective_reasoning

MODEL = os.environ.get('DSH_MODEL') or os.environ.get('ASTRA_MODEL') or DEFAULT_MODEL
EFFORT = effective_reasoning(MODEL, os.environ.get('DSH_EFFORT') or os.environ.get('ASTRA_EXPECTED_EFFORT', 'xhigh'))


def main():
    binary = Path(os.environ.get('DSH_BIN') or shutil.which('dsh') or 'dsh')
    if not binary.is_file() and shutil.which(str(binary)) is None:
        print(json.dumps({'status': 'skipped', 'reason': f'dsh not found: {binary}'}))
        return 0
    try:
        version = subprocess.check_output([str(binary), '--version'], text=True,
                                          stderr=subprocess.DEVNULL, timeout=30).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'failed', 'reason': f'dsh --version failed: {exc}'}))
        return 1
    with tempfile.TemporaryDirectory(prefix='astra-dsh-probe-') as folder:
        folder = Path(folder)
        home = folder/'dsh-home'; home.mkdir()
        overlay = write_overlay(folder/'overlay.yml', provider=DEFAULT_PROVIDER,
                                model=MODEL, reasoning=EFFORT)
        env = {k: v for k, v in os.environ.items() if k in (
            'PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
            'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy')}
        env.update({'HOME': str(folder), 'DSH_HOME': str(home), 'NO_COLOR': '1', 'TERM': 'dumb'})
        command = [str(binary), '--profile', 'headless', '--patch', str(overlay), '--dump-config']
        try:
            dump = subprocess.run(command, cwd=folder, env=env, capture_output=True,
                                  text=True, timeout=120)
        except subprocess.SubprocessError as exc:
            print(json.dumps({'status': 'failed', 'reason': f'dsh --dump-config failed: {exc}'}))
            return 1
        text = dump.stdout
        ok = dump.returncode == 0 and f'model: {MODEL}' in text and f'reasoningEffort: {EFFORT}' in text
        result = {'status': 'ok' if ok else 'failed', 'dsh_binary': str(binary),
                  'dsh_version': version, 'profile': 'headless', 'model': MODEL,
                  'reasoning_effort': EFFORT, 'provider': DEFAULT_PROVIDER,
                  'dump_config_exit': dump.returncode,
                  'overlay': str(overlay)}
        if not ok:
            result['stderr'] = (dump.stderr or '')[-1000:]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
