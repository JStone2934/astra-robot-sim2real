# Plan A: DeepSeek-V4.1-Flash-1017 behind the existing Codex harness

This branch keeps the paper's agent harness (native Codex app-server) and swaps the
model backend to DeepSeek's Responses-compatible API. Only the model, provider,
reasoning level, and model catalog change; the simulation service, subject
workspace, `PROMPT.md`/`API.md`/`PRIOR.md`, `robot.py`, mailbox transport, budgets,
and success criteria are unchanged.

## Configuration

```bash
export ASTRA_CODEX_BIN=/home/js/.local/bin/codex   # >= 0.144.0; default path may not exist
export ASTRA_BASE_URL=https://api.deepseek.com
export ASTRA_API_KEY=sk-...                        # DeepSeek platform key
export ASTRA_MODEL=deepseek-flash                  # API name; V4.1-Flash-1017 is served by it
export ASTRA_MODELS_JSON="$PWD/sim/scripts/deepseek-models.json"
export ASTRA_EXPECTED_MODEL=deepseek-flash         # audits read this
export ASTRA_EXPECTED_EFFORT=xhigh                 # mapped to `max`, see below
```

`prepare_native()` turns this into a private `config.toml` with:

```toml
model = "deepseek-flash"
model_provider = "experiment"
model_reasoning_effort = "max"
model_providers = {"experiment" = {"name" = "Experiment Responses provider",
  "base_url" = "https://api.deepseek.com", "env_key" = "ASTRA_NATIVE_TOKEN",
  "wire_api" = "responses", "supports_websockets" = false,
  "request_max_retries" = 0, "stream_max_retries" = 0}}
```

The key is only injected as `ASTRA_NATIVE_TOKEN` in the app-server process; the
subject shell still runs with `shell_environment_policy.inherit = "none"` and
`network.enabled = false`.

## What changed in this branch

| File | Change |
|---|---|
| `sim/sim_env/model_catalog.py` | Effort mapping (`xhigh`→`max` for DeepSeek) and `models.json` parsing |
| `sim/sim_env/agent.py` | Copies the catalog into the private Codex home; applies the effective effort; records provider/endpoint/catalog metadata and `requested_reasoning` |
| `sim/sim_env/expectations.py` | `ASTRA_EXPECTED_MODEL` / `ASTRA_EXPECTED_EFFORT` for post-trial audits |
| `sim/scripts/run_prior_comparison.py`, `run_skill_comparison.py`, `local_feedback_batch.py`, `local_feedback_repeats.py` | Hard-coded `('gpt-6-astra', 'xhigh')` replaced with the expectation helpers |
| `sim/scripts/account_preflight.py` | Provider mode: checks key, catalog, Codex version, and optionally one minimal Responses call (`--no-inference` to skip) |
| `sim/scripts/check_deepseek_launcher.py` | Zero-cost probe: starts app-server, runs `initialize` + `thread/start`, asserts the resolved model |
| `sim/scripts/deepseek-models.json` | Codex model catalog for `deepseek-flash` (image input, 1M context, `shell_command`, freeform `apply_patch`) |
| `sim/scripts/start_*.py` | `--reasoning` accepts `low`/`max` in addition to the paper's levels |

## Verification

```bash
python -m pytest sim/tests -q
python sim/scripts/account_preflight.py --no-inference     # no tokens
python sim/scripts/check_deepseek_launcher.py             # no model turn
```

Expected probe result:

```json
{"status": "ok", "model": "deepseek-flash", "effective_effort": "max",
 "resolved_model": "deepseek-flash", "match": true}
```

Then a minimal live check (a few tokens):

```bash
python sim/scripts/account_preflight.py
```

A first visual check should follow before a study block: start one short trial and
confirm the agent can open `evidence/*/head.jpg` and report the button colour. If
images do not reach the model, stop here; do not collect trials.

## Running a block

Use the unchanged launchers, e.g.:

```bash
PYTHON="$PWD/.conda/envs/astra/bin/python" python sim/scripts/start_prior_experiment.py \
  ds-i0e0-01 \
  --input-dir sim/studies/body-experience-001/inputs/I0E0 \
  --reference sim/studies/body-experience-001/private-initial-reference.json \
  --setup sim/setups/formal-001.json --max-seconds 1800 --no-video
```

Set `ASTRA_EXPECTED_MODEL=deepseek-flash` and `ASTRA_EXPECTED_EFFORT=xhigh` for the
audit scripts. The effective level recorded in `agent-result.json` is `max`;
`requested_reasoning` keeps the requested `xhigh`.

Prerequisites: `sim/.subject-deps` (NumPy for the subject's `/usr/bin/python3`),
and `--no-video` on hosts without the Playwright Chromium runtime.

## Known limitations and deviations

- **Truncation limit.** The catalog uses `truncation_policy.limit = 100000` tokens
  instead of DeepSeek's published `10000`, so that reading the I2/I3 robot XML is
  not cut off. Record this if comparing with the paper.
- **Harness/persona.** Codex's instructions and tools are unchanged; DeepSeek is
  therefore evaluated inside a GPT-oriented shell. Results are comparable to the
  paper as "same harness, different model", not as the model's best-case setup.
- **Stateless Responses API.** `store`, `previous_response_id`, and `include` are
  ignored by DeepSeek; Codex sends the full history, which is its default.
- **Custom tools.** DeepSeek accepts only the `apply_patch` custom tool; the
  catalog must keep `shell_type = "shell_command"` (function tool) and a Codex
  build >= 0.144.0.
- **Reasoning summary** is not generated; `reasoning.effort` is supported.
- **Network.** The subject remains sandboxed with networking disabled; DeepSeek's
  own API traffic happens in the app-server process.
- **Do not mix numbers.** A DeepSeek block is a new model baseline; report model,
  provider, catalog hash, effective effort, and harness version with the results.
