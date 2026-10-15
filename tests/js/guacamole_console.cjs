"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../../desktop/config/guacamole-console.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));

class Target {
    constructor() { this.listeners = new Map(); }
    addEventListener(name, callback) {
        if (!this.listeners.has(name)) this.listeners.set(name, []);
        this.listeners.get(name).push(callback);
    }
    async emit(name, value = {}) {
        await Promise.all((this.listeners.get(name) || []).map(callback => callback(value)));
    }
}

class Element extends Target {
    constructor() {
        super();
        this.value = ""; this.hidden = false; this.disabled = false;
        this.dataset = {}; this.attributes = {}; this.clientWidth = 800; this.clientHeight = 420;
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}
    scrollIntoView() {}
    replaceChildren() {}
    appendChild() {}
    contains() { return false; }
}

function fixture() {
    const ids = ["screen", "desktop-view", "status", "console-keyboard", "csrf", "connect-form", "connect",
        "password", "font-size", "disconnect", "fullscreen", "keyboard-toggle", "keyboard-panel", "lifetime"];
    const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
    elements["font-size"].value = "12";
    elements.csrf.value = "fixture-csrf";
    const modifier = new Element(); modifier.dataset.modifier = "65507";
    const document = new Target();
    document.getElementById = id => elements[id];
    document.querySelectorAll = () => [modifier];
    const window = new Target(); window.innerHeight = 800; window.visualViewport = new Target();
    const clients = [], pendingStatus = [], timers = new Map();
    let timerId = 0, ticket = 0;
    const response = value => ({ok: true, json: async () => value});
    class Client {
        constructor(tunnel) {
            this.tunnel = tunnel; this.sizes = []; this.keys = [];
            const element = new Element();
            this.display = {getElement: () => element, getWidth: () => 800, scale() {}};
            clients.push(this);
        }
        getDisplay() { return this.display; }
        connect() { this.onstatechange(3); }
        disconnect() { this.onstatechange(5); }
        sendSize(...size) { this.sizes.push(size); }
        sendKeyEvent(...key) { this.keys.push(key); }
    }
    const context = vm.createContext({document, window, Guacamole: {
        Client,
        Keyboard: class { reset() {} },
        Mouse: Object.assign(class { onEach() {} }, {Touchpad: class { onEach() {} }}),
        WebSocketTunnel: class {}, Tunnel: {State: {CLOSED: 2}},
    }, fetch: async (url, options) => {
        const body = JSON.parse(options.body);
        assert.equal(body.csrf, "fixture-csrf");
        if (url.endsWith("/session")) return response({attachment: "ticket-" + ++ticket});
        if (url.endsWith("/status")) return new Promise(resolve => pendingStatus.push(() => resolve(response({expires_in: 3500, idle_expires_in: 580}))));
        return response({disconnected: true});
    }, setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
    clearTimeout: id => timers.delete(id)});
    vm.runInContext(source, context, {filename: "guacamole-console.js"});
    return {elements, window, document, clients, pendingStatus, timers, modifier,
        async connect() {
            elements.password.value = "fixture-password";
            await elements["connect-form"].emit("submit", {preventDefault() {}});
        },
        async runTimers(delay) {
            for (const [id, timer] of [...timers]) if (timer.delay === delay) {
                timers.delete(id); timer.callback();
            }
            await flush();
        },
    };
}

test("a status response from a disconnected session cannot create another poll loop", async () => {
    const ui = fixture();
    await ui.connect();
    await ui.elements.disconnect.emit("click");
    await ui.connect();
    assert.equal(ui.pendingStatus.length, 2);
    ui.pendingStatus[0](); await flush();
    assert.equal([...ui.timers.values()].filter(timer => timer.delay === 5000).length, 0);
    ui.pendingStatus[1](); await flush();
    assert.equal([...ui.timers.values()].filter(timer => timer.delay === 5000).length, 1);
});

test("window and phone viewport events coalesce and unchanged dimensions are not resent", async () => {
    const ui = fixture(); await ui.connect();
    const client = ui.clients[0];
    assert.equal(client.sizes.length, 1);
    for (let width = 390; width < 410; width++) {
        ui.elements.screen.clientWidth = width;
        await ui.window.emit("resize"); await ui.window.visualViewport.emit("resize");
    }
    assert.equal(client.sizes.length, 1);
    await ui.runTimers(100);
    assert.deepEqual(client.sizes, [[800, 440], [409, 440]]);
    await ui.window.emit("resize"); await ui.runTimers(100);
    assert.equal(client.sizes.length, 2);
    await ui.window.emit("resize"); await ui.elements.disconnect.emit("click");
    assert.equal([...ui.timers.values()].filter(timer => timer.delay === 100).length, 0);
});

test("opening the mobile keyboard resizes a fullscreen terminal", async () => {
    const ui = fixture(); await ui.connect();
    ui.document.fullscreenElement = ui.elements["desktop-view"];
    ui.elements.screen.clientHeight = 220;
    await ui.elements["keyboard-toggle"].emit("click"); await ui.runTimers(100);
    assert.deepEqual(ui.clients[0].sizes.at(-1), [800, 220]);
});

test("Unicode input and sticky Ctrl are released when leaving the browser", async () => {
    const ui = fixture(); await ui.connect();
    await ui.modifier.emit("click");
    ui.elements["console-keyboard"].value = "é😀";
    await ui.elements["console-keyboard"].emit("input", {isComposing: false});
    await ui.window.emit("blur");
    assert.deepEqual(ui.clients[0].keys, [[1, 65507], [1, 233], [0, 233], [1, 0x0101F600], [0, 0x0101F600], [0, 65507]]);
    assert.equal(ui.modifier.attributes["aria-pressed"], "false");
});

test("unsupported fullscreen reports a useful message", async () => {
    const ui = fixture(); await ui.connect();
    await ui.elements.fullscreen.emit("click");
    assert.match(ui.elements.status.textContent, /Fullscreen unavailable/);
});
