import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim_env.agent_dsh import EventLog, build_command, run_agent, write_overlay


def test_event_log_projects_stream():
    log = EventLog()
    lines = [
        '{"type":"session","sessionId":"session-1","cwd":"/w"}',
        '{"type":"status","phase":"turn_start","turn":1}',
        '{"type":"tool_call","name":"bash"}',
        '{"type":"tool_result","name":"bash"}',
        '{"type":"status","phase":"turn_end","reason":{"kind":"completed"}}',
        '{"type":"final","text":"answer"}',
    ]
    for line in lines:
        log.consume(line)
    assert log.session_id == 'session-1'
    assert log.tool_calls == 1 and log.tool_results == 1
    assert log.final_text == 'answer'
    assert log.status == 'completed'
    assert log.error is None


def test_event_log_marks_errors_and_malformed_lines():
    log = EventLog()
    log.consume('not json')
    log.consume('{"type":"tool_call","name":"bash"}')
    log.consume('{"type":"status","phase":"turn_end","reason":{"kind":"error","error":{"message":"boom"}}}')
    log.consume('{"type":"final","text":""}')
    assert log.malformed == 1
    assert log.error == 'boom'
    assert log.status == 'failed'


def test_overlay_and_command(tmp_path):
    overlay = write_overlay(tmp_path/'overlay.yml', provider='deepseek-official',
                            model='deepseek-flash', reasoning='max')
    text = overlay.read_text()
    assert 'model: deepseek-flash' in text and 'reasoningEffort: max' in text
    assert 'policy: never' in text
    command = build_command('/usr/bin/dsh', 'headless', overlay)
    assert command == ['/usr/bin/dsh', '--profile', 'headless', '--patch', str(overlay), '--json', '-']


def fake_dsh(path, body):
    path.write_text('#!/usr/bin/env python3\nimport sys, json, time\n'
                    "if '--version' in sys.argv:\n    print('fake-dsh 1.0'); sys.exit(0)\n"
                    'sys.stdin.read()\n' + body)
    path.chmod(0o755)
    return path


def trial_dirs(tmp_path):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    (workspace/'PROMPT.md').write_text('Do the task.')
    private = tmp_path/'private'; private.mkdir()
    return workspace, private


def test_run_agent_completes_and_writes_artifacts(tmp_path):
    workspace, private = trial_dirs(tmp_path)
    dsh = fake_dsh(tmp_path/'dsh', "print(json.dumps({'type':'session','sessionId':'s1'}), flush=True)\n"
                                   "print(json.dumps({'type':'tool_call','name':'bash'}), flush=True)\n"
                                   "print(json.dumps({'type':'tool_result','name':'bash'}), flush=True)\n"
                                   "print(json.dumps({'type':'status','phase':'turn_end','reason':{'kind':'completed'}}), flush=True)\n"
                                   "print(json.dumps({'type':'final','text':'done'}), flush=True)\n")
    result = run_agent(workspace, '/tmp/astra-test-missing.sock', private,
                       model='deepseek-flash', reasoning='xhigh', max_seconds=10, dsh_bin=dsh)
    assert result['status'] == 'completed'
    assert result['exit_code'] == 0
    assert result['session_id'] == 's1'
    assert result['reasoning'] == 'max'
    assert result['requested_reasoning'] == 'xhigh'
    assert (private/'agent-final.txt').read_text() == 'done\n'
    assert len((private/'agent-events.jsonl').read_text().splitlines()) == 5
    assert len((private/'agent-timing.jsonl').read_text().splitlines()) == 2
    assert json.loads((private/'agent-result.json').read_text())['status'] == 'completed'
    audit = json.loads((private/'dsh-config-audit.json').read_text())
    assert 'unrestricted' in audit['subject_network']
    assert 'deepseek-flash' in audit['overlay_text']


def test_run_agent_failure_status(tmp_path):
    workspace, private = trial_dirs(tmp_path)
    dsh = fake_dsh(tmp_path/'dsh', "print(json.dumps({'type':'session','sessionId':'s2'}), flush=True)\n"
                                   "print(json.dumps({'type':'status','phase':'turn_end','reason':{'kind':'error','error':{'message':'boom'}}}), flush=True)\n"
                                   "print(json.dumps({'type':'final','text':''}), flush=True)\n"
                                   'sys.exit(1)\n')
    result = run_agent(workspace, '/tmp/astra-test-missing.sock', private,
                       max_seconds=10, dsh_bin=dsh)
    assert result['status'] == 'failed'
    assert result['exit_code'] == 1
    assert result['error'] == 'boom'


def test_run_agent_stops_on_deadline(tmp_path):
    workspace, private = trial_dirs(tmp_path)
    dsh = fake_dsh(tmp_path/'dsh', "print(json.dumps({'type':'session','sessionId':'s3'}), flush=True)\n"
                                   'time.sleep(30)\n')
    result = run_agent(workspace, '/tmp/astra-test-missing.sock', private,
                       max_seconds=1, dsh_bin=dsh)
    assert result['status'] == 'environment_ended'
    assert result['elapsed_wall_s'] < 10


def test_codex_entrypoint_dispatches_to_dsh(monkeypatch, tmp_path):
    workspace, private = trial_dirs(tmp_path)
    dsh = fake_dsh(tmp_path/'dsh', "print(json.dumps({'type':'session','sessionId':'s4'}), flush=True)\n"
                                   "print(json.dumps({'type':'status','phase':'turn_end','reason':{'kind':'completed'}}), flush=True)\n"
                                   "print(json.dumps({'type':'final','text':'ok'}), flush=True)\n")
    monkeypatch.setenv('ASTRA_RUNNER', 'dsh')
    monkeypatch.setenv('DSH_BIN', str(dsh))
    monkeypatch.setenv('ASTRA_MODEL', 'deepseek-flash')
    from sim_env.agent import run_agent as entrypoint
    result = entrypoint(workspace, '/tmp/astra-test-missing.sock', private,
                        reasoning='xhigh', max_seconds=10)
    assert result['runner'] == 'deepseek-harness-headless'
    assert result['status'] == 'completed'
    assert result['resolved_model'] == 'deepseek-flash'
    assert result['reasoning'] == 'max'
