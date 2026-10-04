"""Fixed, reviewable operations exposed by the panel's administration screen."""

from __future__ import annotations


ADMIN_UNIT = "basaltwater-admin-maintenance.service"
ADMIN_HELPER = "/opt/basaltwater/common/service_tools/admin_job.py"
ADMIN_STATE = "/var/lib/basaltwater-privilege-broker/admin-job.json"
ADMIN_TIMEOUT = 6 * 60 * 60

# Descriptions are also the effects reviewed at the independent approval origin.
ADMIN_ACTIONS = {
    "update-packages": {
        "title": "Update system packages", "group": "Updates",
        "effect": "Refresh APT indexes and install available Debian package upgrades. Keep existing configuration files. Package hooks can restart services; no distribution upgrade, autoremove, or automatic reboot.",
    },
    "refresh-preview": {
        "title": "Check Basaltwater refresh", "group": "Updates",
        "effect": "Check that Basaltwater can repeat this machine's saved setup without downloading or applying updates. The result reports whether validation passed.",
    },
    "refresh": {
        "title": "Refresh Basaltwater", "group": "Updates",
        "effect": "Update Basaltwater and repeat this machine's last successful Debian setup. Services and active agent work may be interrupted; configuration will be reconciled.",
    },
    "check-web": {
        "title": "Check web configuration", "group": "Services",
        "effect": "Run nginx -t to validate the installed web configuration. Do not reload it.",
    },
    "reload-web": {
        "title": "Reload web gateway", "group": "Services",
        "effect": "Validate with nginx -t, then reload nginx.service only if validation succeeds. Hosted routes may briefly reconnect.",
    },
    "restart-panel": {
        "title": "Restart web panel", "group": "Services",
        "effect": "Restart basaltwater-web-panel.service. The panel and its prompt scheduler will stop; active prompts will be interrupted. Saved tasks and history remain on disk.",
    },
    "reboot": {
        "title": "Restart host", "group": "Power",
        "effect": "Schedule a host reboot with shutdown -r +2. All sessions and running work will be interrupted after two minutes. Cancel before that deadline if needed.",
    },
    "shutdown": {
        "title": "Shut down host", "group": "Power",
        "effect": "Schedule host power-off with shutdown -h +2. All sessions and running work will be interrupted after two minutes. Powering on again requires the VM controller or physical access.",
    },
    "cancel-shutdown": {
        "title": "Cancel scheduled power action", "group": "Power",
        "effect": "Run shutdown -c to cancel a still-pending scheduled reboot or power-off. This cannot undo a shutdown that has begun.",
    },
}


def action_spec(action: object) -> dict[str, str]:
    if not isinstance(action, str) or action not in ADMIN_ACTIONS:
        raise ValueError("Unknown administration action")
    return ADMIN_ACTIONS[action]


def dispatch_argv(action: str) -> list[str]:
    """Start a detached system service; never accept a program or shell text."""
    action_spec(action)
    if action == "cancel-shutdown":
        return ["/usr/sbin/shutdown", "-c"]
    return [
        "/usr/bin/systemd-run", "--no-ask-password", "--quiet", "--collect",
        f"--unit={ADMIN_UNIT}", "--property=Type=exec",
        f"--property=RuntimeMaxSec={ADMIN_TIMEOUT}", "--property=TimeoutStopSec=30",
        "--property=KillMode=control-group", "--property=UMask=0077",
        "--property=StandardInput=null", "--property=StandardOutput=null",
        "--property=StandardError=null", "--working-directory=/", "--uid=root",
        "--", "/usr/bin/python3", "-I", ADMIN_HELPER, action,
    ]
