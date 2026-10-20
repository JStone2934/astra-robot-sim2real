"""Expected model identity for post-trial audits.

The frozen paper batches assume ``gpt-6-astra`` at ``xhigh``.  A DeepSeek block
runs the same scripts with:

    ASTRA_EXPECTED_MODEL=deepseek-flash
    ASTRA_EXPECTED_EFFORT=xhigh      # mapped to the provider's ``max``

so the audit still rejects trials whose model, effort, or budget changed.
"""
import os

from .model_catalog import effective_reasoning

DEFAULT_MODEL = 'gpt-6-astra'
DEFAULT_EFFORT = 'xhigh'


def expected_model():
    return os.environ.get('ASTRA_EXPECTED_MODEL', DEFAULT_MODEL)


def expected_effort():
    requested = os.environ.get('ASTRA_EXPECTED_EFFORT', DEFAULT_EFFORT)
    return effective_reasoning(expected_model(), requested)
