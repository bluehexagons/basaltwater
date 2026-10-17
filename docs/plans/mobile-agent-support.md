# Mobile device support status

Status: deferred. Basaltwater does not provision mobile SDKs, emulator hosts,
or simulators and does not enable T3 device access. There is no implementation
timeline.

T3 owns device discovery, streaming, control, and its device tools. Device
pairing enrolls a T3 client; it does not authorize simulator control. T3's
first device listing can install tools on a configured SSH host, so device
discovery must be an explicit, authorized action. See the [T3 device guide](https://github.com/pingdotgg/t3code/blob/v0.1.0/docs/user/devices.md).

Any future support requires a concrete mobile project and an authorized,
disposable device host. Android needs an eligible Linux/KVM host; iOS simulation
needs an operator-provided Mac with Xcode. Require verified SSH host keys,
key-based non-interactive access, and explicit device selection. Never disable
host-key checking or copy a private key into a project. Require explicit scope
for install, reset, or power operations. Preserve personal devices and data.
Keep build and launch recipes in the application repository. Responsive browser
checks do not qualify native mobile behavior.
