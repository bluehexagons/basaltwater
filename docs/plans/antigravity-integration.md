# Google Antigravity integration

## Status

Google AI Pro and Ultra use Antigravity CLI (`agy`) for subscription-backed
Gemini access. Google ended consumer access through the original Gemini CLI on
June 18, 2026. Gemini Code Assist Standard and Enterprise and separately billed
API access have their own paths. [Google's migration announcement](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/) · [Antigravity plans](https://antigravity.google/docs/plans)

T3 Code already offers an interactive Antigravity provider. Use the existing
[T3 setup and sign-in](../agents/t3-providers.md#provider-sign-in) if the goal is
to run Gemini in T3 threads. T3 installs and updates its managed runtime on the
selected environment and owns the provider instance's login. Basaltwater's
standalone CLI installation, SSH login command, and inventory are proposed
below. Live qualification is pending for both paths.

## Initial scope

Offer opt-in installation on managed Debian agent hosts, direct terminal use
by the account holder, and read-only installation and version diagnostics.
Use [Claude Code](claude-code-integration.md) as the scope ceiling, with
credential storage, model selection, and usage left to `agy`. The account
entitlements determine the Gemini catalog. Use the live model selector and
`/usage` to inspect offered models and quota. [Google model availability](https://antigravity.google/docs/models) · [CLI command reference](https://antigravity.google/docs/cli/reference/)

For consumer subscriptions, support direct user prompts in Google's terminal
CLI or in T3's native Antigravity ACP integration. Google distinguishes that
interactive ACP use from external agents orchestrating `agy`, even when a
human approves the delegation. Keep consumer OAuth out of task workers,
scheduled prompts, cross-provider delegation, token proxies, credential
imports, and account rotation. Gemini API and Enterprise setup are outside
the initial milestone. [Google ACP documentation](https://antigravity.google/docs/ide/extensions/acp/) · [Google's interactive ACP clarification](https://discuss.ai.google.dev/t/tos-clarification-is-human-supervised-codex-to-antigravity-acp-delegation-permitted/200697/2) · [Antigravity terms](https://www.antigravity.google/terms) · [Antigravity FAQ](https://antigravity.google/docs/faq/)

## Proposed implementation

1. **Install as the target user.** Add `--antigravity` to the supported Debian
   agent profiles and preserve the selection in remote setup and saved replay.
   Reuse `lib/vendor_installer.py`, the user-scoped install helper in
   `common/agent_steps.py`, and the shared profile extension in
   `plugins/common.py`. Use Google's Unix installer and verify its shell,
   package, and supported platform requirements before wiring it in. Keep an
   existing executable under its current owner. For Antigravity-only T3 hosts,
   make the explicit provider selection satisfy the applicable readiness
   profile without invoking Codex setup or authorization. [Google CLI installation](https://antigravity.google/docs/getting-started?tab=cli)
2. **Document native terminal sign-in.** On the target, run `agy` or
   `agy acp --login` over SSH. The CLI prints a URL, the account holder opens it
   in their browser, and they paste Google's one-time code into the target
   prompt. A small follow-up could expose this as `basaltw agent auth login
   HOST USER --tool antigravity` if the native interactive command works with
   Basaltwater's SSH policy. Reuse host and identity validation from
   `lib/agent_login.py`. Preserve strict host-key checks and scope any required
   PTY to the attended login command. Keep OAuth exchange and persistence in
   `agy` and keep codes and tokens out of logs and receipts. Setup and replay
   can print the sign-in command and complete without a browser or a human
   waiting at the controller. [Google SSH authentication](https://antigravity.google/docs/install?tab=cli#remote-ssh-oauth-flow) · [Terminal login command](https://antigravity.google/docs/ide/extensions/acp/#flags)
3. **Report installation independently of login.** Teach tool discovery and
   doctor to resolve `agy` for the target user and report its version. Use
   a verified, non-secret native login signal if one is available. Otherwise
   leave authentication unverified and direct the user to the actual terminal
   or T3 provider. Keep T3's managed runtime and account status in T3.
4. **Add narrow maintenance only if it fits.** Google documents `agy update`.
   Add it to the managed updater only after verifying ownership, background
   self-update behavior, and compatibility with Basaltwater's preflight and
   recovery. Continue to use T3's updater for its managed runtime. Project
   skills can use `.agents/skills` in T3. Defer managed user-wide skills,
   context tuning, and additional workstation platforms until a concrete need
   arises. [Google CLI update](https://antigravity.google/docs/cli/commands/changelog) · [T3 Antigravity runtime and skills](https://github.com/pingdotgg/t3code/blob/v0.1.11/docs/user/providers-antigravity.md)

## Acceptance

- Offline checks cover opt-in profile selection, dry runs, remote and saved
  replay, install-as-user and existing-executable cases, version detection,
  and Codex-only task provider validation. If the SSH shortcut is implemented,
  cover noninteractive refusal, strict host validation, cancellation,
  disconnect, and code redaction with a fake executable and temporary homes.
- In an attended disposable single-user Debian VM, install as the target user,
  sign in over SSH with an eligible account, run a Gemini turn in a synthetic
  repository, and confirm the chosen plan and quota in the native UI. Check
  the actual launch settings for an API or Cloud billing override, and keep
  Google One credit overage disabled for the test. Reopen the session to check
  persistence and use `/logout` before retiring the VM. Keep raw auth output
  and account details outside Git. [Google CLI authentication](https://antigravity.google/docs/install?tab=cli) · [CLI settings and quota](https://antigravity.google/docs/cli/reference/)
- Qualify the existing T3 path separately on a supported environment. Use its
  managed runtime and Google account sign-in, confirm the live Gemini catalog
  and one direct user turn, then sign out. Record exact versions and evidence
  type in the [coverage map](testing-coverage.md) after verification.
