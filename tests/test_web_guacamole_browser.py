"""Offline regressions for Guacamole browser controls and console lifecycle."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


class ConsoleBrowserTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser script checks")
    def test_console_client_behavior(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "--test", str(root / "tests/js/guacamole_console.cjs")],
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DesktopBrowserTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser script checks")
    def test_permission_controls_and_browser_lifecycle(self):
        source = Path(__file__).resolve().parents[1] / "desktop/config/guacamole.js"
        result = subprocess.run([shutil.which("node"), "-e", DESKTOP_CLIENT_CHECK, str(source)],
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


DESKTOP_CLIENT_CHECK = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const source = require("node:fs").readFileSync(process.argv[1], "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
class Element {
    constructor() {
        this.listeners = {}; this.attributes = {}; this.dataset = {};
        this.value = ""; this.disabled = false; this.hidden = true;
        this.clientWidth = 800; this.clientHeight = 450;
    }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    async emit(name, event = {}) { for (const callback of this.listeners[name] || []) await callback(event); }
    click() { return this.emit("click"); }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {} replaceChildren() {} appendChild() {}
    contains() { return false; }
}
function fixture(agents = true) {
    const elements = new Map();
    const byId = id => {
        if (!agents && ["disable", "override-duration", "agent-control-status"].includes(id)) return null;
        if (!elements.has(id)) elements.set(id, new Element());
        return elements.get(id);
    };
    byId("csrf").value = "test-csrf"; byId("zoom").value = "fit";
    if (agents) byId("override-duration").value = "900";
    const document = new Element(), window = new Element();
    document.getElementById = byId; document.querySelectorAll = () => [];
    document.querySelector = () => agents ? {} : null;
    const calls = [], timers = new Map(), clients = [];
    let saved = {agent_control: "enabled", override_remaining: null, override_until: null,
        control_revision: "initial", paused: false, controlled: false, expires_in: 3500, idle_expires_in: 590};
    class Client {
        constructor() {
            this.keys = [];
            this.display = {getElement: () => new Element(), getWidth: () => 800,
                getHeight: () => 450, scale() {}};
            clients.push(this);
        }
        getDisplay() { return this.display; }
        connect() { this.onstatechange(3); }
        disconnect() { this.onstatechange(5); }
        sendKeyEvent(...args) { this.keys.push(args); }
    }
    class Mouse { onEach() {} }
    const context = vm.createContext({document, window, Guacamole: {Client,
        Mouse: Object.assign(Mouse, {Touchpad: Mouse, Touchscreen: Mouse}),
        Keyboard: class { reset() {} }, WebSocketTunnel: class {}, Tunnel: {State: {CLOSED: 2}}},
        fetch: async (url, options) => {
            const body = JSON.parse(options.body); calls.push({url, body});
            assert.equal(body.csrf, "test-csrf");
            if (url.endsWith("/session")) return {ok: true, json: async () => ({attachment: "test-ticket"})};
            if (url.endsWith("/control")) {
                if (body.action === "take") Object.assign(saved, {controlled: true, paused: true,
                    agent_control: "human-override", override_remaining: body.override_seconds ?? null});
                else if (body.action === "observe") saved.controlled = false;
                else Object.assign(saved, {controlled: false, agent_control: body.action === "disable" ? "disabled" : "enabled",
                    paused: body.action === "disable"});
            }
            if (url.endsWith("/disconnect")) saved.controlled = false;
            return {ok: true, json: async () => ({...saved, state: "running", geometry: [800, 450]})};
        }, setTimeout: callback => { const id = Symbol(); timers.set(id, callback); return id; },
        clearTimeout: id => timers.delete(id)});
    vm.runInContext(source, context);
    return {byId, window, saved, calls, clients,
        async connect() { await byId("connect-form").emit("submit", {preventDefault() {}}); },
        async poll() { const callbacks = [...timers.values()]; timers.clear(); for (const callback of callbacks) await callback(); },
        actions() { return calls.filter(call => call.url.endsWith("/control")).map(call => call.body); }};
}
(async () => {
    const ui = fixture(); await flush();
    assert.match(ui.byId("agent-control-status").textContent, /Agents enabled/);
    await ui.connect();
    assert.equal(ui.byId("logout").disabled, true);
    await ui.byId("take").click();
    assert.equal(ui.byId("logout").disabled, false);
    assert.equal(ui.actions().at(-1).override_seconds, 900);
    assert.match(ui.byId("agent-control-status").textContent, /15 min 0 sec remaining/);
    assert.match(ui.byId("status").textContent, /Browser connected/);
    await ui.byId("observe").click();
    assert.equal(ui.saved.agent_control, "human-override");
    ui.byId("override-duration").value = "indefinite";
    await ui.byId("take").click();
    assert.equal(ui.actions().at(-1).override_seconds, null);
    assert.match(ui.byId("agent-control-status").textContent, /until you enable agents/);
    await ui.byId("disable").click();
    assert.equal(ui.actions().at(-1).action, "disable");
    assert.match(ui.byId("agent-control-status").textContent, /Agents disabled/);
    assert.equal(ui.byId("desktop-keyboard").disabled, true);
    await ui.byId("disconnect").click();
    assert.equal(ui.saved.agent_control, "disabled");
    assert.match(ui.byId("agent-control-status").textContent, /Agents disabled/);
    Object.assign(ui.saved, {agent_control: "enabled", paused: false});
    await ui.poll();
    assert.match(ui.byId("agent-control-status").textContent, /Agents enabled/);
    await ui.connect(); await ui.byId("resume").click();
    assert.equal(ui.actions().at(-1).action, "resume");
    assert.match(ui.byId("agent-control-status").textContent, /Agents enabled/);
    await ui.byId("take").click();
    Object.assign(ui.saved, {agent_control: "enabled", controlled: false, paused: false});
    await ui.poll();
    assert.equal(ui.byId("desktop-keyboard").disabled, true);
    assert.equal(ui.byId("take").attributes["aria-pressed"], "false");
    const mutations = ui.actions().length;
    await ui.window.emit("pagehide"); await flush();
    assert.equal(ui.actions().length, mutations);
    assert.equal(ui.saved.agent_control, "enabled");
    const ordinary = fixture(false); await ordinary.connect();
    await ordinary.byId("take").click();
    assert.equal(ordinary.actions().at(-1).override_seconds, null);
    await ordinary.byId("resume").click();
    assert.equal(ordinary.actions().at(-1).action, "observe");
    assert.equal(ordinary.calls.some(call => call.url.endsWith("/inspect")), false);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
