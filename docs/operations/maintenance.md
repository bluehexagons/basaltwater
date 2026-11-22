# Recurring Maintenance

Setup flows install recurring jobs as managed systemd units. The jobs are
enabled and restarted after their unit files are written, so rerunning setup
immediately applies a changed schedule. Replacement units are staged atomically
so an existing working timer is not removed before its replacement is ready.
Randomized delays are used where appropriate to avoid many hosts running the
same job at the same instant.

## Choose a topic

<a id="installed-jobs"></a>
<a id="update-policy"></a>
- [Installed maintenance jobs and update policy](maintenance-timers.md) Timers, package policy, reboots, and observable state.

<a id="cleanup-and-state-safety"></a>
- [Maintenance cleanup and recovery](maintenance-state.md) State safety, cleanup, and transaction recovery.

<a id="agent-maintenance-holds"></a>
<a id="related-configuration"></a>
- [Agent maintenance holds and configuration](maintenance-holds.md) Bounded maintenance holds and related host configuration.
