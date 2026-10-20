import json
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim_env.agent import prepare_native


def host_home(tmp_path, model):
    home = tmp_path/'host-codex'
    home.mkdir()
    (home/'config.toml').write_text(
        f'model = "{model}"\nmodel_provider = "openai"\n'
        '[model_providers.openai]\nname = "OpenAI"\n'
        'base_url = "https://chatgpt.example/backend-api/codex"\n'
        'env_key = "HOST_KEY"\nwire_api = "responses"\n')
    (home/'auth.json').write_text(json.dumps({'auth_mode': 'chatgpt', 'tokens': {'account_id': 'x'}}))
    (home/'models.json').write_text((ROOT/'scripts'/'deepseek-models.json').read_text())
    return home


def prepare(tmp_path, monkeypatch, **env):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    private = tmp_path/'private'; private.mkdir()
    for key in ('ASTRA_BASE_URL', 'ASTRA_API_KEY', 'ASTRA_MODEL', 'ASTRA_MODELS_JSON', 'CODEX_HOME'):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    home, config, app_env = prepare_native(workspace, '/tmp/unused.sock', private, reasoning='xhigh')
    return home, config, app_env


def test_provider_mode_copies_catalog_and_maps_effort(tmp_path, monkeypatch):
    host = host_home(tmp_path, 'deepseek-flash')
    home, config, app_env = prepare(tmp_path, monkeypatch, CODEX_HOME=host,
                                    ASTRA_BASE_URL='https://api.deepseek.com',
                                    ASTRA_API_KEY='EXAMPLE_KEY', ASTRA_MODEL='deepseek-flash')
    assert config['model'] == 'deepseek-flash'
    assert config['model_provider'] == 'experiment'
    assert config['model_providers']['experiment']['base_url'] == 'https://api.deepseek.com'
    assert config['model_reasoning_effort'] == 'max'
    written = tomllib.loads((home/'config.toml').read_text())
    assert written['model'] == 'deepseek-flash'
    assert written['model_reasoning_effort'] == 'max'
    assert (home/'models.json').is_file()
    assert not (home/'auth.json').exists()
    assert app_env['ASTRA_NATIVE_TOKEN'] == 'EXAMPLE_KEY'
    shell = written['shell_environment_policy']['set']
    assert shell['ROBOT_MAILBOX'].endswith('.robot-mailbox')
    assert 'ASTRA_NATIVE_TOKEN' not in shell and 'EXAMPLE_KEY' not in json.dumps(shell)
    permissions = written['permissions']['subject']
    assert permissions['network']['enabled'] is False


def test_host_mode_keeps_model_and_reasoning(tmp_path, monkeypatch):
    host = host_home(tmp_path, 'gpt-6-astra')
    home, config, app_env = prepare(tmp_path, monkeypatch, CODEX_HOME=host)
    assert config['model'] == 'gpt-6-astra'
    assert config['model_reasoning_effort'] == 'xhigh'
    assert (home/'auth.json').is_file()
    assert not (home/'models.json').exists()


def test_explicit_catalog_is_used_for_non_deepseek_model(tmp_path, monkeypatch):
    host = host_home(tmp_path, 'gpt-6-astra')
    catalog = ROOT/'scripts'/'deepseek-models.json'
    home, config, _ = prepare(tmp_path, monkeypatch, CODEX_HOME=host, ASTRA_MODELS_JSON=catalog)
    assert (home/'models.json').read_text() == catalog.read_text()
    assert config['model_reasoning_effort'] == 'xhigh'


def test_missing_explicit_catalog_fails(tmp_path, monkeypatch):
    host = host_home(tmp_path, 'deepseek-flash')
    with pytest.raises(FileNotFoundError):
        prepare(tmp_path, monkeypatch, CODEX_HOME=host,
                ASTRA_MODELS_JSON=tmp_path/'missing.json')
