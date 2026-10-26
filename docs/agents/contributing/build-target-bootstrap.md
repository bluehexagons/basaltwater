# Minimal typed-target bootstrap

`common.build_target_bootstrap.bootstrap_build_target(config)` is an internal
setup phase for controller-driven artifact delivery. The reserved custom step
`bootstrap_build_target` is dispatched by `remote_setup.py` before runtime
inference, profile composition or post-setup deployment. It accepts only the
minimal target enrollment intent, selected username/machine and dry-run mode;
deployment, disable and unrelated executable setup options are refused.

Bootstrap installs the protected typed helper through the existing setup
transport and calls the managed target installer. It does not save machine
state, replace the saved application profile, create a normal setup operation,
run repository commands or activate an application. The installer's durable
ownership marker and serving hold retain partial enrollment evidence. The
controller must retain its separate delivery journal and only run the final
full profile after artifact delivery. This internal phase does not itself wire
`--build-on` or provide automatic recovery.

Runtime staging owns the existing setup bootstrap/runtime locks. Before source
activation, `prepare_target_runtime(..., bootstrap_build_target=True)` takes
the normal setup-operation ownership and refuses any unfinished setup marker,
including explicit recovery state. The bootstrap dispatch repeats that check
under ownership before calling the installer. A malformed marker also refuses.
Successful bootstrap leaves the previous saved profile and machine state intact.
Ordinary setup retains its existing explicit recovery path; an `in_progress`
setup now stops runtime displacement before its code can be replaced.

When typed-target enrollment exists, runtime activation takes its exclusive
installer maintenance lease through source replacement and Syncthing ACL
repair. Missing, unsafe or busy coordination refuses before replacement.
The lease is released before the new runner invokes enrollment or retained
artifact replay, which acquire their own exclusive ownership. Lock order is
setup bootstrap/runtime ownership, setup-operation ownership, then typed-target
maintenance. No exclusive target lease is held across controller typed calls.
This coordinates the setup transport, not unrelated root edits or every other
source-update entry point.

Dry runs validate the minimal enrollment and report bootstrap without opening
target operation state or changing files. They do not establish live host
readiness. The focused tests use temporary directories and mocked installer,
process and native boundaries, including a real parser/configuration round trip.
