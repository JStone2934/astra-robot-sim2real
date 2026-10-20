"""Model-catalog and reasoning helpers shared by the Codex launcher and preflight.

DeepSeek's Responses provider uses ``low``/``high``/``max`` reasoning levels and
needs a model catalog entry (``models.json``) that declares image input, the
context window, and the shell tool type.  Codex's other providers keep their own
vocabulary, so the mapping is keyed on the model id rather than applied globally.
"""
import json
from pathlib import Path

# Codex/OpenAI effort name -> DeepSeek Responses effort name.  ``xhigh`` is the
# setting used by the frozen study; DeepSeek's nearest level is ``max``.
DEEPSEEK_EFFORTS = {'low': 'low', 'medium': 'high', 'high': 'high',
                    'xhigh': 'max', 'max': 'max'}


def effective_reasoning(model, requested):
    """Return the effort string the selected provider accepts.

    ``requested`` is preserved in the trial record so a study manifest can say
    "xhigh was requested; max was sent" without guessing.
    """
    if not isinstance(requested, str) or not requested:
        raise ValueError('reasoning effort must be a non-empty string')
    if str(model).lower().startswith('deepseek'):
        return DEEPSEEK_EFFORTS.get(requested, requested)
    return requested


def catalog_slugs(path):
    """Return the declared model slugs, or ``None`` when the file is unusable."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    models = data.get('models') if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None
    slugs = [row.get('slug') for row in models if isinstance(row, dict)]
    return [slug for slug in slugs if isinstance(slug, str)]


def catalog_declares(path, model):
    slugs = catalog_slugs(path)
    return bool(slugs) and model in slugs
