"""Authenticated Headscale forms; all native access belongs to the root broker."""

from __future__ import annotations

import html

from common.service_tools.web_panel_templates import render_document
from lib.headscale_portal import validate_action
from lib.privilege_client import exchange


def submit(fields: dict[str, str]) -> dict:
    action = fields.get("action")
    if action == "register":
        if set(fields) != {"action", "user", "auth_id", "verified"} or fields["verified"] != "yes":
            raise ValueError("Verify the person and exact Auth ID")
        validate_action({"action": action, "user": fields["user"], "token": "0" * 32})
        return exchange({"action": "headscale-register", "user": fields["user"], "auth_id": fields["auth_id"]})
    parameters = validate_action(fields)
    return exchange({"action": "request", "operation": "headscale.admin", "parameters": parameters,
                     "reason": "Administrator requested Headscale action"})


def render(state, style: str) -> str:
    escape = lambda value: html.escape(str(value), quote=True)
    csrf = '<input type="hidden" name="csrf" value="' + escape(state.csrf_token) + '">'

    def form(action: str, inputs: str, label: str) -> str:
        return ('<form class="diagnostic-filters headscale-form" method="post" action="/actions/headscale">' + csrf
                + '<input type="hidden" name="action" value="' + action + '">' + inputs
                + '<button>' + label + ' — request review</button></form>')

    content = ('<p>Approval admits a device to the network’s shared trust boundary. New networks default to all-to-all access; '
                'an adopted network retains its policy. Verify the intended person and exact pending Auth ID through an authenticated channel. '
                'Hostname alone is insufficient.</p><p>The one-hour machine bootstrap key lifetime is separate from native device expiry. '
                'Observed expiry below comes from Headscale. Expiring or deleting a node changes native access; verify revocation on the client.</p>'
                '<p>Every change requires a separate privilege review. Login and T3 pairing do not approve changes. '
                '<a href="/admin?view=requests">Inspect submitted requests and outcomes</a>. '
                'After an unconfirmed registration, inspect nodes before starting and verifying a fresh native registration. Never automatically retry.</p>')
    try:
        inventory = exchange({"action": "headscale-inspect"})
        users, nodes = inventory["users"], inventory["nodes"]
    except (OSError, ValueError, RuntimeError, KeyError):
        users, nodes = [], []
        content += '<p class="empty">Headscale inspection unavailable. Check broker access and native service over SSH.</p>'
    content += '<section><h2>People</h2>'
    for user in users:
        owned = sum(node.get("user_id") == user["id"] for node in nodes)
        content += '<article class="event"><strong>' + escape(user["name"]) + '</strong><p>User ID ' + escape(user["id"]) + f' · {owned} nodes</p>'
        if owned:
            content += '<p>Remove this person’s nodes before deleting their user.</p>'
        else:
            content += form("users_delete", '<input type="hidden" name="id" value="' + escape(user["id"]) + '">', 'Delete person')
        content += '</article>'
    content += form("users_create", '<label>Person name<input name="name" required maxlength="128" autocomplete="off"></label>', 'Create person') + '</section>'
    options = ''.join('<option value="' + escape(user["id"]) + '">' + escape(user["name"]) + ' (ID ' + escape(user["id"]) + ')</option>' for user in users)
    if options:
        content += '<section><h2>Approve a verified personal device</h2>' + form("register",
            '<label>Person<select name="user">' + options + '</select></label>'
            '<label>Exact pending Auth ID<input type="password" name="auth_id" required maxlength="256" autocomplete="off"></label>'
            '<label><input type="checkbox" name="verified" value="yes" required> I verified this person and exact Auth ID</label>',
            'Register device') + '</section>'
    content += '<section><h2>Enrolled nodes</h2>'
    for node in nodes:
        content += '<article class="event"><h3>' + escape(node["hostname"]) + '</h3><dl>'
        for label, key in (("ID", "id"), ("Native node ID", "node_id"), ("Owner user ID", "user_id"),
                           ("Addresses", "addresses"), ("Tags", "tags"), ("Online", "online"), ("Observed device expiry", "expiry")):
            value = node.get(key)
            content += '<dt>' + label + '</dt><dd>' + escape(', '.join(value) if isinstance(value, list) else value if value is not None else 'Not reported') + '</dd>'
        content += '</dl>'
        for action, label in (("node_expire", "Expire node"), ("node_delete", "Delete / revoke node")):
            content += form(action, '<input type="hidden" name="id" value="' + escape(node["id"]) + '">', label)
        content += '</article>'
    content += '</section>'
    return render_document(title="Headscale administration", style=style +
                           '.headscale-form button{white-space:normal}.headscale-form input[type=checkbox]{display:inline;width:auto;min-height:0}',
                           header='<header><p class="eyebrow">Network administration</p><h1>Headscale</h1></header>',
                           content=content, navigation=state.navigation("headscale"), footer='<footer>Managed by Basaltwater</footer>')
