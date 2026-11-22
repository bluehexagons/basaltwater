# Web panel UX and live status overhaul

Status: complete. See the [completion and qualification record](web-panel-overhaul-completion.md#completion-and-qualification-record)
for the delivered scope, measured cost, and validation limits.

## Purpose

Make current host and service state easier to trust and follow without asking
operators to reload pages. Add a small, well-bounded status interface and
progressive browser polling while retaining the panel's server-rendered pages,
existing authentication and authorization boundaries, and no-JavaScript
navigation and forms.

Start with polling. Consider server-sent events or another push transport only
if measurements show that short requests cannot meet a real latency or load
need. Do not move the panel to a single-page application or add a web framework
as part of this work unless a measured requirement justifies that change.

## Choose a topic

<a id="baseline-audit-findings"></a>
<a id="product-and-technical-decisions"></a>
<a id="implementation-sequence"></a>
<a id="1-establish-source-contracts-and-a-baseline"></a>
<a id="2-add-a-bounded-status-read-model"></a>
<a id="3-build-the-browser-update-layer-and-refresh-the-dashboard"></a>
<a id="4-extend-polling-to-selected-detail-views"></a>
<a id="5-reduce-coupling-and-qualify-the-rollout"></a>
<a id="acceptance-criteria"></a>

- [Baseline, decisions, delivery, and acceptance](web-panel-overhaul-baseline-decisions-delivery-acceptance.md)

<a id="completion-and-qualification-record"></a>
<a id="reviewed-budgets-and-measurements"></a>
<a id="validation-and-deployment"></a>
<a id="follow-up-review"></a>
<a id="final-integration-review"></a>
<a id="out-of-scope"></a>

- [Completion and qualification record](web-panel-overhaul-completion.md)
