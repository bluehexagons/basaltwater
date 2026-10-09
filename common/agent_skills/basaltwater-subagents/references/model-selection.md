# Cost-aware subagents and project learning

This workflow applies to Debian agent VMs and CachyOS workstations. The commands
below work in bash and fish; use the actual project path on either platform.

Use OpenAI models currently exposed by the session. Start with Luna for file
classification, extraction, and bounded documentation edits; use Sol for coding,
integration reasoning, and substantive reviews. Reserve Astra for a specialized
task where its expected benefit justifies its cost. A small independent task
does not need the parent's model or thinking level.

For trivial classification, consider `none` only when the exact model **and the
delegation tool** expose it; otherwise use `low`. Sol usually starts at `medium`;
raise effort only for demonstrated ambiguity or difficulty. These are starting
points, not a claim that a model family always wins on every project. Current
[OpenAI model selection](https://developers.openai.com/api/docs/guides/model-selection)
and [reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning)
describe model-dependent support. Plan/account access and the runtime tool's
available settings may be narrower than API documentation.

Always choose standard service. Never select fast mode (including a `priority`
service-tier alias), `max`, or `ultra` autonomously, inherit them into a worker,
or use them as an automatic retry. If the user explicitly selects one, explain
the likely extra cost and ask for confirmation when their intent is ambiguous
or the expected benefit is weak. A specific, already-confirmed selection is
authorization; do not ask repeatedly. The recommendation command deliberately
has no expensive-setting override. An explicit user exception is configured in
the session itself and recorded separately from the standard-service dataset.

Check the actual delegation interface before launching. If model overrides
require a fresh context, supply the bounded task, project instructions, file
ownership, worktree path, acceptance checks, and necessary evidence in that
context. If the harness only supports inheriting the parent model, report that
limitation and perform a small task locally when delegation would waste cost.
If the interface fixes workers to fast service or cannot honor a required
standard tier, report that limit and use a supported standard-service session.
Never imply that a recommendation changes the running session or tool settings.

## Project policy and outcomes

The parent owns `.basaltwater/agent-models.json` and
`.basaltwater/agent-model-results.json` in the **primary checkout**. Commands
resolve linked worktrees to that same checkout. Review and commit these non-secret
project files within the user's authorized scope; workers report observations
to the parent rather than concurrently edit them.

If the project ignores `.basaltwater/`, inspect its ignore rules before staging.
When evidence should travel with the repository, allow only these two files;
installation-channel state and unrelated local data must stay ignored. For
example, put these rules after an existing directory exclusion in the project's
`.gitignore`, within the authorized scope:

```gitignore
!/.basaltwater/
/.basaltwater/*
!/.basaltwater/agent-models.json
!/.basaltwater/agent-model-results.json
```

Stage the two reviewed files explicitly. Do not force-add the whole directory.

```bash
basaltw agent models init ~/repos/PROJECT --json
basaltw agent models recommend ~/repos/PROJECT classification --available SESSION_MODELS.json --json
basaltw agent models record ~/repos/PROJECT --task classification --model gpt-6-luna --effort low --outcome accepted --validation 'Parent checked every category against the agreed rules' --json
basaltw agent models report ~/repos/PROJECT --json
```

`init` creates a starter policy and refuses to overwrite an existing one. Its
catalog is a dated seed that requires review, not a live availability assertion.
`--available` must describe this session's actual delegation choices:

```json
[
  {"id": "gpt-6-luna", "efforts": ["low", "medium", "high", "xhigh"]},
  {"id": "gpt-6.1-sol", "efforts": ["low", "medium", "high", "xhigh"]}
]
```

The command also accepts Codex's public `models_cache.json` shape, with `slug`,
`visibility`, and `supported_reasoning_levels`. That cache describes terminal
Codex selection; use it only when it represents the task's actual interface.
Do not substitute a broad API model list for a subagent tool's availability.

Only models with fresh verified metadata and a supported safe effort are
eligible. The task's requested effort is a minimum. A provisional setting uses
the lowest supported safe effort at or above it; classification `none` therefore
falls back to `low` when the tool cannot disable thinking. Proven settings at
higher supported efforts also compete using their own evidence and costs, so
successful `high` results are not hidden by an unsuccessful `medium` default.
Untested higher efforts are not automatic retries. Change the task minimum only
after reviewing its needs. Inspect the returned effort and cost before launch.
No eligible choice returns nonzero and `selection: null`.

`accepted` means the parent accepted the initial result after validation;
`reworked` means correction was needed; `failed` includes unusable results and
escalations. Record failures as well as successes. Use `--attempts`, `--seconds`,
and `--rework-minutes` when observed. `--cost-usd` is optional: leave unavailable
billing unknown, including subscription sessions without dollar telemetry. Also
leave unobserved runtime and parent rework unknown; explicitly record zero when
you checked and found no rework. Reports expose the known rework subtotal and
sample count, and return a null total when observations are incomplete.
Never invent a cost or treat missing cost as zero. Each cost should cover all
attempts represented by that outcome; record another model's escalation as
another outcome. Validation should describe acceptance evidence without prompts,
credentials, or confidential source excerpts.

Results remain separate by task class, evaluation version, exact model ID, and
effort. Set task-specific acceptance criteria and update its `evaluation` value
when those criteria, prompts, harness behavior, platform-dependent checks, or the
workload materially change. Separate Debian-specific and CachyOS-specific task
classes when their acceptance requirements differ. Old versions remain visible
in `report` but do not drive current choices.
Split easy file classification from ambiguous classification rather than average
them into a misleading score.

## Adapt to newly available models

At the start of a delegation session, and when access or model options change:

1. Inspect the actual exposed model IDs and supported efforts. Export a fresh
   availability file for `recommend`; availability alone says nothing about price
   or suitability.
2. Act on `uncatalogued_models`, `stale_models`, and `refresh_required`: inspect
   current official OpenAI metadata, access, reasoning support, and standard
   pricing. Add or revise the relevant project catalog entries with their exact
   ID, OpenAI provider, Luna/Sol/Astra role, safe efforts, rates, source URL, and
   `verified_on` date. Do not guess a new model's role from its name or reset the
   date without checking its source. Rerun the recommendation after refresh.
3. Evaluate a promising candidate on representative bounded tasks using the
   same inputs and acceptance criteria as the incumbent. Include tool use,
   format adherence, correctness, runtime, retries, and parent rework. Keep a
   proven incumbent while gathering evidence; an expensive candidate needs a
   concrete reason, especially Astra.
4. Record reviewed outcomes and compare results. The defaults require at least
   three recent samples with 80% initial acceptance before an alternative is
   treated as proven. These small-sample thresholds are configurable project
   conventions, not statistical proof. Use more evidence for consequential work.
5. Promote a candidate when its observed effectiveness and total cost justify
   it. Exact model IDs keep a new release from inheriting an older release's
   success history. Roll back a regression and retain the failed observations.

The selector chooses the cheapest proven setting that meets the project quality
threshold. If all proven choices have complete cost observations, it compares
total observed cost divided by initially accepted outcomes, including failed
attempt costs. Otherwise it compares standard token-cost estimates consistently
for every candidate using the task's expected input/output tokens. Estimates
omit variable reasoning, caching, context surcharges, tools, and subscription
accounting; review the reported runtime and rework as well. Sparse alternatives
are returned as `trial_candidates`, not silently promoted over proven choices.

With no proven setting, a provisional choice starts at the task's family or a
stronger available family; poorly performing settings with enough evidence are
excluded. Astra remains excluded unless `--astra-reason` explains the benefit.
The default catalog lifetime is 30 days and the evidence window is 90 days.
Recheck sooner after releases, deprecations, pricing/access changes, or repeated
failures. Preserve older evidence for history while checking recent results.

New IDs and revised project defaults need no Basaltwater release. Basaltwater
still maintains the shipped seed and workflow when provider schemas, role
assumptions, or tool interfaces change. Discovery and representative evaluation
are agent responsibilities; the local selector does not contact a provider,
run an evaluation, spend money, or automatically switch a session.
