# Plan B: DeepSeek Harness (DSH) as the trial runner

This branch replaces the Codex app-server with `dsh --profile headless --json`
for one trial per invocation. The simulation service, subject workspace,
`PROMPT.md`/`API.md`/`PRIOR.md`, `robot.py`, mailbox transport, budgets, and
success criteria are unchanged; the agent shell, system prompt, tools, context
management, and sandbox are DSH's.

## Why a separate runner

Codex and DSH differ in the things a study normally holds fixed:

| | Codex (Plan A) | DSH (Plan B) |
|---|---|---|
| System prompt | Codex instructions template | DSH coding-agent persona |
| Tools | `shell_command`, `apply_patch`, `view_image` | `bash`, `read`/`write`/`edit`, `read_image`, skills, subagents |
| Context | Codex compaction + `models.json` | DSH compaction/basic + token meter |
| Sandbox | custom Codex permission profile (files + network off) | DSH `bash-sandbox` (files only) |
| Credential | `~/.codex/auth.json` or provider env | `DEEPSEEK_API_KEY` |

Plan B therefore measures a different system: it is the model's native harness,
not the paper's harness. Keep its results in a separate block.

## Configuration

```bash
export ASTRA_RUNNER=dsh
export DSH_BIN=$(command -v dsh)          # local DSH >= 0.2.1-rc.1 recommended
export DEEPSEEK_API_KEY=sk-...            # ASTRA_API_KEY is accepted as a fallback
export DSH_MODEL=deepseek-flash           # optional; default
export DSH_EFFORT=xhigh                   # optional; mapped to max
```

`run_agent()` in `sim/sim_env/agent.py` dispatches on `ASTRA_RUNNER`; every
launcher keeps working unchanged.

Each trial writes a private `dsh-home`, a profile overlay, and the same artifacts
as the Codex path:

| Artifact | Contents |
|---|---|
| `dsh-config-audit.json` | binary/version, model, provider, effort, overlay, curated environment, network caveat |
| `agent-result.json` | `runner`, `status`, `exit_code`, `session_id`, `model`, `resolved_model`, `reasoning`, `requested_reasoning`, tool counts, timing |
| `agent-events.jsonl` | every NDJSON event with a receive timestamp |
| `agent-timing.jsonl` | session start/end |
| `agent-final.txt` | final answer text |

The overlay selects the model and disables interactive approval while keeping the
`workspace-write` sandbox:

```yaml
- id: agent-default-model
  config:
    provider: deepseek-official
    model: deepseek-flash
    reasoningEffort: max
- id: approval
  config:
    policy: never
```

## Verification

```bash
python -m pytest sim/tests -q                 # includes a fake-DSH end-to-end test
python sim/scripts/check_dsh_runner.py        # composes the profile, no model call
```

Expected:

```json
{"status": "ok", "dsh_version": "0.2.0-rc.2", "model": "deepseek-flash",
 "reasoning_effort": "max"}
```

Without a key, a real run fails closed and records the failure:

```json
{"status":"failed","error":"llm-deepseek: no API key ... (MISSING_CREDENTIAL)"}
```

## Running a block

The launchers are unchanged:

```bash
ASTRA_RUNNER=dsh DSH_BIN="$(command -v dsh)" DEEPSEEK_API_KEY=sk-... \
ASTRA_EXPECTED_MODEL=deepseek-flash ASTRA_EXPECTED_EFFORT=xhigh \
PYTHON="$PWD/.conda/envs/astra/bin/python" python sim/scripts/start_prior_experiment.py \
  ds-dsh-i0e0-01 \
  --input-dir sim/studies/body-experience-001/inputs/I0E0 \
  --reference sim/studies/body-experience-001/private-initial-reference.json \
  --setup sim/setups/formal-001.json --max-seconds 1800 --no-video
```

Prerequisites: `sim/.subject-deps` (NumPy for the subject's `/usr/bin/python3`)
and `--no-video` on hosts without Playwright Chromium.

## Known limitations and deviations

- **Subject networking.** DSH's bash sandbox confines file writes but does not
  restrict network access. The subject has no web tools, but a `curl` from bash
  would still work. This does not meet the frozen protocol's "no external
  information" rule and is recorded as `subject_network` in the audit. Enforce a
  network namespace at the process level if that rule must hold.
- **Harness confound.** Persona, tools, compaction, and approval behavior differ
  from Codex. Do not compare Plan B percentages directly with the paper's
  GPT-6-Astra numbers.
- **Sandbox availability.** `dsh-bash-sandbox` needs a working Landlock/bubblewrap
  backend; without one, commands fail with `SANDBOX_UNAVAILABLE` rather than
  running unconfined.
- **Version.** The local DSH is `0.2.0-rc.2`; DeepSeek recommends `0.2.1-rc.1+`
  for V4.1-Flash-1017. Upgrade before collecting a formal block.
- **Workflow page.** Only session start/end are projected into
  `agent-workflow.jsonl`; the operator page will not show a full tool-call feed.
- **Session storage.** DSH persists sessions under the per-trial `dsh-home`;
  they are private to the trial and not part of the released evidence.
- **Do not mix numbers.** Report runner, DSH version, model, effort, and the
  network caveat with every Plan B result.
