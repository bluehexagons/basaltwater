# AI Agent Quick Start

These instructions apply to the repository. Read `docs/agents/contributing/README.md`
and its linked references when a task needs more detail. Higher-priority user,
developer, and system instructions still govern the session.

## Before Editing

1. Run `git status -sb` and preserve unrelated worktree changes.
2. Read every target file completely before changing it.
3. Use `apply_patch` for edits and stage only the files in the requested scope.
4. Review `git diff --check` and the final diff before committing.

## Essential Pattern

```python
from __future__ import annotations
from lib.machine_state import can_modify_kernel

def harden_kernel() -> None:
    """Apply a kernel-specific setup step when the machine permits it."""
    if not can_modify_kernel():
        print("  ✓ Skipping (container)")
        return
    # Implementation
```

## Critical Rules

1. **Always** use `from __future__ import annotations`
2. **Never** commit secrets or credentials
3. **Always** validate inputs with the appropriate validator: use
   `lib/validation.py` for paths, packages, network/configuration values, and
   `lib/validators.py` for hosts, IPs, and usernames.
4. **Never** break function signatures without updating all callers
5. **Always** read complete file before changing
6. **Always** keep docs up to date when changing patterns
7. **Always** remove unused code — don't keep for "API compatibility"
8. **Always** use the machine capability helper that matches the operation;
   `can_modify_kernel()` is not a blanket check for packages or services
9. **Always** mock system calls in tests and use temporary directories

## Quick Commands

```bash
# Check syntax
python3 -m py_compile file.py
sh -n install.sh
git diff --check

# Dry run test
basaltw setup server_web test.com --dry-run

# Run tests
python3 -m unittest discover -s tests
```

## Key Files

| Purpose | File | Import | Key Functions |
|---------|------|--------|---------------|
| Configuration | `lib/config.py` | `SetupConfig` | `SetupConfig` |
| Runtime configuration | `lib/runtime_config.py` | `RuntimeConfig` | `RuntimeConfig` |
| Types | `lib/types.py` | `JSONDict`, `StrList` | `JSONDict`, `StrList`, `MaybeStr` |
| Machine State | `lib/machine_state.py` | `is_container`, `can_modify_kernel` | `is_container`, `can_modify_kernel`, `is_vm` |
| Validation | `lib/validation.py` | `validate_filesystem_path` | `validate_filesystem_path`, `validate_package_name` |
| Host/user validation | `lib/validators.py` | `validate_host` | `validate_host`, `validate_ip_address`, `validate_username` |
| SSH Operations | `lib/remote_utils.py` | `run` | `run`, `is_service_active` |
| Setup composition | `plugins/*.py` | `PluginDefinition` | Step builders and capability extensions |
| Self-setup | `lib/orchestrator_bootstrap.py` | `run_orchestrator_bootstrap` | Local package/bootstrap flow |
| antistatic_server | `lib/config.py` | `SetupConfig` | `SetupConfig.antistatic_server` |
| antistatic_db | `lib/config.py` | `SetupConfig` | `SetupConfig.antistatic_db` |
| SSH Keys | `lib/config.py` | `SetupConfig` | `SetupConfig.ssh_key` |

## Setup Boundaries

- `basaltw setup` parses and validates a user-facing configuration, then
  uploads or stages the source for `remote_setup.py`.
- `remote_setup.py` executes target-side setup steps selected by the plugin
  registry; it is the boundary for remote system mutations.
- `bootstrap`/`self-setup` configures the local orchestration host through
  `lib/orchestrator_bootstrap.py`. The shell installer forwards options placed
  before `--setup` or `--local-setup` to that bootstrap command.

When adding a setup feature, put the implementation in its owning package,
wire it through the relevant `plugins/*.py` builder or capability extension,
add focused tests, and update the command/reference documentation.

---

**See `docs/agents/contributing/README.md` for detailed patterns and workflows**
