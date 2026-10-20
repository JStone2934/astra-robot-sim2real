"""DeepSeek Harness (DSH) runner: one headless task per trial.

Plan B keeps the repository's simulation service, subject workspace, prompts, and
``robot.py``/mailbox transport, but replaces the Codex app-server with
``dsh --profile headless --json``.  The runner records the same trial artifacts as
the Codex path (``agent-result.json``, ``agent-events.jsonl``, timing, workflow)
so the study launchers and audits keep working.

Known difference from the Codex runner: DSH's bash sandbox confines file writes to
the workspace but does not disable networking.  The subject receives no web tools,
and this limitation is recorded in ``dsh-config-audit.json``.
"""
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time

from .model_catalog import effective_reasoning

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSH = shutil.which('dsh') or 'dsh'
DEFAULT_MODEL = 'deepseek-flash'
DEFAULT_PROVIDER = 'deepseek-official'


def write_overlay(path, *, provider, model, reasoning):
    """Write a profile patch that selects the provider, model, and reasoning level.

    The approval policy is left at the composition default: the shipped
    ``workspace-write`` preset bundles it with the sandbox, and an unmatched
    sandbox/approval pair makes the permission service fail to activate.
    """
    lines = ['- id: agent-default-model', '  config:',
             f'    provider: {provider}', f'    model: {model}']
    if reasoning:
        lines.append(f'    reasoningEffort: {reasoning}')
    Path(path).write_text('\n'.join(lines)+'\n')
    return Path(path)


def build_command(dsh, profile, overlay):
    command = [str(dsh), '--profile', profile]
    if overlay:
        command += ['--patch', str(overlay)]
    command += ['--json', '-']
    return command


class EventLog:
    """Project the headless NDJSON stream into fields the trial record needs."""

    def __init__(self):
        self.session_id = None
        self.final_text = None
        self.turn_reason = None
        self.error = None
        self.tool_calls = 0
        self.tool_results = 0
        self.malformed = 0
        self.events = 0
        self.usage = {}

    def consume(self, line):
        line = line.strip()
        if not line:
            return None
        try:
            event = json.loads(line)
        except ValueError:
            self.malformed += 1
            return None
        if not isinstance(event, dict):
            self.malformed += 1
            return None
        self.events += 1
        kind = event.get('type')
        if kind == 'session':
            self.session_id = event.get('sessionId') or self.session_id
        elif kind == 'tool_call':
            self.tool_calls += 1
        elif kind == 'tool_result':
            self.tool_results += 1
        elif kind == 'status':
            if event.get('phase') == 'step_end' and isinstance(event.get('usage'), dict):
                for key, value in event['usage'].items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        self.usage[key] = self.usage.get(key, 0)+value
            if event.get('phase') == 'turn_end':
                self.turn_reason = event.get('reason')
                reason = self.turn_reason or {}
                if isinstance(reason, dict) and reason.get('kind') == 'error':
                    self.error = (reason.get('error') or {}).get('message') or 'turn failed'
        elif kind == 'final':
            self.final_text = event.get('text') or ''
        elif kind == 'error':
            self.error = event.get('message') or 'runner error'
        return event

    @property
    def status(self):
        if self.error:
            return 'failed'
        if self.final_text is None:
            return 'failed'
        return 'completed'


def run_agent(workspace, socket_path, private_dir, *, model=None, reasoning='xhigh',
              max_seconds=3600., prompt=None, profile='headless', dsh_bin=None):
    workspace = Path(workspace).resolve()
    private_dir = Path(private_dir).resolve()
    model = model or os.environ.get('DSH_MODEL') or os.environ.get('ASTRA_MODEL') or DEFAULT_MODEL
    provider = os.environ.get('DSH_PROVIDER', DEFAULT_PROVIDER)
    requested_reasoning = reasoning
    effective = effective_reasoning(model, reasoning)
    dsh = Path(dsh_bin or os.environ.get('DSH_BIN') or DEFAULT_DSH)
    if not dsh.is_file() and shutil.which(str(dsh)) is None:
        raise RuntimeError(f'DSH binary not found: {dsh} (set DSH_BIN)')

    dsh_home = private_dir/'dsh-home'
    dsh_home.mkdir(mode=0o700, exist_ok=False)
    overlay = write_overlay(private_dir/'dsh-overlay.yml', provider=provider, model=model,
                            reasoning=effective)
    prompt = prompt if prompt is not None else (workspace/'PROMPT.md').read_text()
    workspace_tmp = workspace/'tmp'; workspace_tmp.mkdir(exist_ok=True)

    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
        'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY',
        'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy')}
    env.update({'HOME': str(workspace), 'TMPDIR': str(workspace_tmp),
                'DSH_HOME': str(dsh_home), 'DSH_PERMISSION_MODE': 'workspace-write',
                'PYTHONPATH': str(ROOT/'.subject-deps'),
                'ROBOT_MAILBOX': str(workspace/'.robot-mailbox'),
                'PYTHONDONTWRITEBYTECODE': '1', 'NO_COLOR': '1', 'TERM': 'dumb',
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': str(workspace/'.gitconfig')})
    key = os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('ASTRA_API_KEY')
    if key:
        env['DEEPSEEK_API_KEY'] = key

    try:
        version = subprocess.check_output([str(dsh), '--version'], text=True,
                                          stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        version = None
    command = build_command(dsh, profile, overlay)
    result = {'runner': 'deepseek-harness-headless', 'dsh_version': version,
              'dsh_binary': str(dsh), 'model': model, 'model_provider': provider,
              'resolved_model': model, 'resolved_model_source': 'configured',
              'reasoning': effective, 'requested_reasoning': requested_reasoning,
              'context_management': 'DSH headless defaults',
              'subject_network': 'unrestricted (DSH bash sandbox confines files only)',
              'status': 'starting', 'start_unix': time.time()}
    (private_dir/'dsh-config-audit.json').write_text(json.dumps({
        **result, 'command': command, 'dsh_home': str(dsh_home),
        'overlay': str(overlay), 'overlay_text': overlay.read_text(),
        'subject_environment': sorted(env)}, indent=2)+'\n')

    from .mailbox import Mailbox
    mailbox = Mailbox(workspace/'.robot-mailbox', socket_path)
    log = EventLog()
    started = time.monotonic()
    timed_out = False
    stderr_path = private_dir/'dsh-stderr.log'
    try:
        with stderr_path.open('w') as stderr, \
             (private_dir/'agent-events.jsonl').open('w') as events, \
             (private_dir/'agent-timing.jsonl').open('w') as timing, \
             (private_dir/'agent-workflow.jsonl').open('w') as workflow:
            def publish(row):
                workflow.write(json.dumps(row, ensure_ascii=False)+'\n'); workflow.flush()
            publish({'event': 'session/started', 'monotonic': time.monotonic(), 'model': model})
            process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=stderr, text=True,
                                       start_new_session=True)
            process.stdin.write(prompt if prompt.endswith('\n') else prompt+'\n')
            process.stdin.close()
            incoming = queue.Queue()
            def reader():
                try:
                    for line in process.stdout:
                        incoming.put(line)
                finally:
                    incoming.put(None)
            threading.Thread(target=reader, daemon=True).start()
            deadline = started+max_seconds
            timing.write(json.dumps({'event': 'session/started', 'monotonic': started})+'\n')
            while True:
                if process.poll() is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    break
                try:
                    line = incoming.get(timeout=.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                event = log.consume(line)
                if event is not None:
                    events.write(json.dumps({'received_monotonic': time.monotonic(), 'event': event},
                                            ensure_ascii=False)+'\n'); events.flush()
            if timed_out:
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=10)
            exit_code = process.wait(timeout=10)
            # Drain anything the reader buffered after the poll noticed exit.
            while True:
                try: line = incoming.get_nowait()
                except queue.Empty: break
                if line is None: break
                log.consume(line)
            ended = time.monotonic()
            status = ('environment_ended' if timed_out else
                      'completed' if exit_code == 0 and log.error is None and log.final_text is not None
                      else 'failed')
            timing.write(json.dumps({'event': 'session/ended', 'monotonic': ended,
                                     'status': status})+'\n')
            publish({'event': 'session/ended', 'monotonic': ended, 'status': status})
    finally:
        mailbox.close()
    final_text = log.final_text or ''
    (private_dir/'agent-final.txt').write_text(final_text+'\n')
    result.update({
        'status': status,
        'exit_code': exit_code, 'session_id': log.session_id,
        'events': log.events, 'malformed_events': log.malformed,
        'usage': log.usage or None,
        'tool_calls': log.tool_calls, 'tool_results': log.tool_results,
        'turn_reason': log.turn_reason, 'error': log.error,
        'elapsed_wall_s': round(ended-started, 3), 'end_unix': time.time(),
        'final_text_bytes': len(final_text.encode())})
    (private_dir/'agent-result.json').write_text(json.dumps(result, indent=2)+'\n')
    return result
