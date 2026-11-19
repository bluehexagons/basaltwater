# Web panel UX and live status overhaul

Status: complete. See the [completion and qualification record](#completion-and-qualification-record)
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

## Completion and qualification record

This plan is split across the following documents; original section anchors remain in place.

- [Baseline, decisions, delivery, and acceptance](web-panel-overhaul-baseline-decisions-delivery-acceptance.md#baseline-audit-findings)
- [Completion and qualification record](web-panel-overhaul-completion.md#completion-and-qualification-record)
