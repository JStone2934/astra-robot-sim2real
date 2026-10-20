import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim_env.model_catalog import DEEPSEEK_EFFORTS, catalog_declares, catalog_slugs, effective_reasoning


def test_deepseek_effort_mapping():
    assert effective_reasoning('deepseek-flash', 'xhigh') == 'max'
    assert effective_reasoning('deepseek-flash', 'medium') == 'high'
    assert effective_reasoning('deepseek-flash', 'max') == 'max'
    assert effective_reasoning('gpt-6-astra', 'xhigh') == 'xhigh'
    assert effective_reasoning('gpt-6-astra', 'medium') == 'medium'


def test_unknown_deepseek_effort_passes_through():
    assert effective_reasoning('deepseek-flash', 'minimal') == 'minimal'


def test_catalog_reading(tmp_path):
    path = tmp_path/'models.json'
    path.write_text(json.dumps({'models': [{'slug': 'deepseek-flash'}, {'slug': 'deepseek-v4-pro'}]}))
    assert catalog_slugs(path) == ['deepseek-flash', 'deepseek-v4-pro']
    assert catalog_declares(path, 'deepseek-flash')
    assert not catalog_declares(path, 'gpt-6-astra')


def test_broken_or_missing_catalog(tmp_path):
    broken = tmp_path/'broken.json'
    broken.write_text('{not json')
    assert catalog_slugs(broken) is None
    assert not catalog_declares(broken, 'deepseek-flash')
    assert catalog_slugs(tmp_path/'missing.json') is None
