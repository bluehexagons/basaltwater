# Mobile device support status

Status: deferred. Basaltwater does not provision mobile SDKs, emulator hosts,
or simulators and does not enable T3 device access. There is no implementation
timeline.

T3 owns device discovery, streaming, control, and its device tools. Device
pairing enrolls a T3 client; it does not authorize simulator control. T3's
first device listing can install tools on a configured SSH host, so device
discovery must be an explicit, authorized action. See the [T3 device guide](https://github.com/pingdotgg/t3code/blob/v0.1.4/docs/user/devices.md).

Stable T3 0.1.4 also offers Simlock pools as an opt-in alpha under **Settings →
Labs**. Basaltwater does not provision or qualify the gateway, workers, or
leases. Each T3 server needs its own agent token; treat that token as shell
access to the workers and use T3's private secret input when needed. Returning
a lease wipes the device. Follow the tagged guide's worker/tool versions and
network requirements only for an explicitly requested disposable-device task;
existing client pairing does not authorize enabling a pool.

Any future support requires a concrete mobile project and an authorized,
disposable device host. Android needs an eligible Linux/KVM host; iOS simulation
needs an operator-provided Mac with Xcode. Require verified SSH host keys,
key-based non-interactive access, and explicit device selection. Never disable
host-key checking or copy a private key into a project. Require explicit scope
for install, reset, or power operations. Preserve personal devices and data.
Keep build and launch recipes in the application repository. Responsive browser
checks do not qualify native mobile behavior.
