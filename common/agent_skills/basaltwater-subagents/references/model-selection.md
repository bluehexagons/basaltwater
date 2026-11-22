# Cost-aware subagent selection

This workflow applies to Debian agent VMs, CachyOS workstations, and Ubuntu
under Windows Subsystem for Linux (WSL). For CLI commands that maintain
project evaluation records, see [project model policy](model-policy.md).

## Subscription cost and performance

Reviewed **2026-11-17**. Prefer an available subscription route when its included
usage covers the task. Check the active login/billing route, remaining allowance,
reset time, and model access before comparing candidates. A subscription is not
unlimited: parallel workers consume allowance too, and paid credits or an API
key can introduce additional charges. Do not switch authentication, buy credits,
or enable overage merely to obtain a recommended model.

GPT-6.2 Sol and Astra launched in ChatGPT Work, Codex, and the API. Access and
speed options depend on the client, plan and workspace settings. See the
[ChatGPT and Codex changelog](https://learn.chatgpt.com/docs/changelog).

Optimize for an accepted result within the user's time and usage budget.
Use GPT-6.2 Sol for routine coding and Opus 5.5 for demanding Claude work.
GPT-6.2 Astra's lower rates and stronger performance on complex coding and
professional artifacts make it a candidate for a bounded trial when the task
has a specific unmet need. Use project outcomes to justify promotion. Apply
the same evidence standard to Fable and check its separate credit requirements.

A stronger included model can cost less overall when it avoids failed attempts
and parent rework; a cheaper worker helps only when its result passes review.
Compare acceptance, elapsed time, retries, rework and allowance consumption.
Keep dollar costs unknown without billing telemetry. Do not divide a monthly
subscription price into an invented per-task charge or mix API estimates and
subscription observations as if they were the same measurement.

[Codex pricing](https://learn.chatgpt.com/docs/pricing) lists approximate Plus
and Standard Business local-message ranges per five hours of 350–3,000 for
Luna, 35–285 for Terra, 17–180 for GPT-6.2 Sol, and 6–50 for GPT-6.2 Astra.
The ranges depend on the task and context. Local and cloud usage share an
allowance. Check the live dashboard for actual usage and reset times.
For Claude, check `/model` and the account's usage display: Fable can require
separate credits even with a subscription. See
[Claude billing and model access](https://code.claude.com/docs/en/model-config).

## Current workload choices

These are starting hypotheses for evaluation, not a benchmark ranking. Exact
IDs and supported settings must come from the active tool. Preserve an explicit
user choice and project evidence; availability in public docs is insufficient.

| Workload | Candidate | Tradeoff |
| --- | --- | --- |
| Bounded extraction, classification, small edits, named checks | GPT-6 Luna; Claude Haiku 5.5 | Low allowance pressure and latency; give narrow inputs and observable checks |
| Writing, image/screen interpretation, app work | GPT-6.1 Terra | Efficient middle option; evaluate coding separately rather than assume Sol-level coding performance |
| Broad coding, integration, substantive review | GPT-6.2 Sol | Start at high for this workload and retain a proven project setting pending representative trials |
| Routine Claude coding and focused review | Claude Sonnet 5.5 | Faster option with lower usage than Opus; use project outcomes to decide whether it suffices |
| Difficult debugging, architecture, long agentic coding and review | Claude Opus 5.5 | Recommended higher-end Claude choice; subscription affordability makes it practical when stronger reasoning helps |
| Difficult cross-service debugging, complex frontend work, or template-driven artifacts | GPT-6.2 Astra | Trial against Sol or Opus when the workload warrants it, and verify the rendered result or end-to-end behavior |
| Exceptional Claude workload | Claude Fable 5.2 | Require a concrete benefit over Opus and check separate credit billing |

Provider sources: [OpenAI model roles](https://learn.chatgpt.com/docs/models),
[GPT-6.2 launch evaluations](https://openai.com/index/introducing-gpt-6-2-astra-sol/),
[Terra capabilities](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[Claude coding guidance](https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code),
and [Claude model comparison](https://platform.claude.com/docs/en/models/overview).
These sources describe different workloads; they do not establish a universal
cross-provider winner. Check fresh provider evidence after releases, then run
representative project trials before changing a proven policy.

OpenAI's GDPval v2 evaluation of professional workflows reports a 6.2-point
gain for GPT-6.2 Astra with 22% fewer output tokens than GPT-6 Astra, and a
5.3-point gain for GPT-6.2 Sol over GPT-6.1 Sol. For Basaltwater trials, compare
the same repository checks, rendered outputs or tool results before measuring
usage and rework. See the [launch evaluations](https://openai.com/index/introducing-gpt-6-2-astra-sol/).

For API fallback, standard uncached input/output USD per million tokens are
Luna $0.10/$0.50, Terra $1/$5, GPT-6.2 Sol $2/$10, and GPT-6.2 Astra $8/$40.
Claude rates are $0.10/$0.50 for Haiku 5.5 up to 100K input, $2/$10 for Sonnet
5.5, $4/$20 for Opus 5.5, and $8/$40 for Fable 5.2. These omit caches,
reasoning volume, tools and context surcharges.
See [context and pricing boundaries](https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/agent-context.md)
for exact model sources, long-prompt rates and client limits. A large inherited
history can erase the savings of a cheap subagent; send a bounded fresh brief
when the interface supports it. Leave headroom for tool results and output.

For ongoing GPT-6.2 API conversations, keep the existing message prefix and
append steering as new turns. OpenAI documents that prefix edits and model
downgrades can discard retained reasoning. In Codex or T3, send corrections as
follow-ups and retain a concise checkpoint before switching models or restarting
a provider session. Give longer tasks explicit acceptance checks and authority
to make routine, reversible decisions within scope. See
[GPT-6.2 prompting and migration guidance](https://developers.openai.com/api/docs/guides/latest-model).

## OpenAI selector defaults

Use OpenAI models currently exposed by the session. Luna is a provisional
starting point at `medium` for bounded classification, extraction, documentation,
small fixes, focused tests, and app-script or export validation. Explicit,
deterministic trivial work may use `low` when inputs and verification are
straightforward. Use Luna `high` or `xhigh` only for a concrete complex task.
Keep broad coding, integration, ambiguous cross-file changes, and substantive
reviews on GPT-6.2 Sol `high`. Use Astra for a specific unmet need, and record
the outcome to establish whether it improves on Sol or Opus. Preserve existing
project policy and check the live session. Match the worker to its own task.

Check that the exact model **and the delegation tool** expose the chosen
setting. Current
[OpenAI model selection](https://developers.openai.com/api/docs/guides/model-selection)
and [reasoning guidance](https://developers.openai.com/api/docs/guides/reasoning)
describe model-dependent support. Plan/account access and the runtime tool's
available settings may be narrower than API documentation.

Choose Standard service by default. Fast and Ultrafast are speed settings.
`max` and `ultra` are reasoning efforts. Keep these higher-cost settings out of
autonomous choices, inherited worker settings and automatic retries. If the
user explicitly selects one, explain
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

The local selector currently accepts OpenAI entries with Luna/Sol/Astra roles.
Its shipped seed covers those three coding choices; it does not rank Claude,
model subscription allowances, or discover Terra automatically. Use the table
above and runtime capabilities for those decisions. Do not relabel Terra as Sol
solely to satisfy a schema or treat the selector's API estimate as a subscription
recommendation. Changing selector roles or billing support is a separate code
change requiring validation.

## Choose a topic

<a id="project-policy-and-outcomes"></a>
<a id="adapt-to-newly-available-models"></a>
- [Project model policy and outcomes](model-policy.md) Initialize, recommend, record results, and qualify new models.
