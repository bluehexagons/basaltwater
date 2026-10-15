/* Guacamole SSH console: one account, responsive terminal and mobile input. */
"use strict";
(() => {
    const byId = id => document.getElementById(id);
    const screen = byId("screen"), view = byId("desktop-view");
    const status = byId("status"), text = byId("console-keyboard");
    const csrf = byId("csrf").value;
    const buttons = [...document.querySelectorAll("[data-key],[data-modifier]")];
    const modifiers = new Set();
    let client = null, attachment = null, connected = false, busy = false;
    let revision = 0, active = true, timer = null, failure = "";
    const dimensions = () => ({width: Math.max(240, Math.min(3840, Math.floor(screen.clientWidth))),
        height: Math.max(180, Math.min(2160, Math.floor(document.fullscreenElement === view
            ? screen.clientHeight : window.innerHeight * .55)))});
    async function post(path, data = {}, keepalive = false) {
        const result = await fetch(`/console/${path}`, {method: "POST", credentials: "same-origin",
            cache: "no-store", keepalive, headers: {"Content-Type": "application/json"},
            body: JSON.stringify({csrf, ...data})});
        if (!result.ok) throw new Error(await result.text());
        return result.json();
    }
    function controls() {
        byId("connect-form").hidden = connected;
        byId("connect").disabled = busy || client !== null;
        byId("password").disabled = busy || client !== null;
        byId("font-size").disabled = busy || client !== null;
        byId("disconnect").disabled = busy;
        byId("fullscreen").disabled = !connected;
        byId("keyboard-toggle").disabled = !connected;
        text.disabled = !connected;
        for (const button of buttons) button.disabled = !connected;
    }
    const keyboard = new Guacamole.Keyboard(screen);
    function releaseKeys() {
        keyboard.reset();
        if (client) for (const key of modifiers) client.sendKeyEvent(0, key);
        modifiers.clear();
        for (const button of buttons) if (button.dataset.modifier) button.setAttribute("aria-pressed", "false");
        text.value = "";
    }
    function reset() {
        releaseKeys();
        const previous = client;
        client = null; attachment = null; connected = false;
        revision++;
        clearTimeout(timer); timer = null;
        if (previous) previous.disconnect();
        screen.replaceChildren();
        byId("keyboard-panel").hidden = true;
        byId("keyboard-toggle").setAttribute("aria-expanded", "false");
        byId("lifetime").textContent = "Sessions end after one hour or ten minutes without input.";
        controls();
    }
    function resize() {
        if (!client || !connected) return;
        const size = dimensions();
        client.sendSize(size.width, size.height);
        const display = client.getDisplay();
        if (display.getWidth()) display.scale(Math.min(1, screen.clientWidth / display.getWidth()));
    }
    async function poll() {
        const current = client, ticket = attachment;
        if (!connected) return;
        try {
            const result = await post("status", {session: ticket});
            if (client !== current) return;
            byId("lifetime").textContent = `Session ends in ${Math.ceil(result.expires_in / 60)} min; inactivity ends in ${Math.ceil(result.idle_expires_in / 60)} min.`;
        } catch (error) {
            if (client === current) { reset(); status.textContent = error.message; }
        } finally { if (connected) timer = setTimeout(poll, 5000); }
    }
    byId("connect-form").addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || client) return;
        busy = true; controls();
        const attempt = ++revision;
        status.textContent = "Connecting to console…"; failure = "";
        try {
            let password = byId("password").value;
            byId("password").value = "";
            const pending = post("session", {password, ...dimensions(), font_size: Number(byId("font-size").value)});
            password = "";
            const result = await pending;
            if (!active || revision !== attempt) {
                await post("disconnect", {session: result.attachment}).catch(() => {});
                return;
            }
            attachment = result.attachment;
            const tunnel = new Guacamole.WebSocketTunnel("/console/ws");
            const current = new Guacamole.Client(tunnel);
            client = current;
            const display = current.getDisplay();
            // Use Guacamole 1.6's Image path; its ImageDecoder path can stall.
            display.drawStream = (layer, x, y, stream, mimetype) => {
                const reader = new Guacamole.DataURIReader(stream, mimetype);
                reader.onend = () => display.draw(layer, x, y, reader.getURI());
            };
            screen.appendChild(display.getElement());
            const sendMouse = event => {
                if (!connected || client !== current) return;
                if (byId("keyboard-panel").hidden) screen.focus({preventScroll: true});
                current.sendMouseState(event.state, true);
            };
            const mouse = new Guacamole.Mouse(display.getElement());
            const touch = new Guacamole.Mouse.Touchpad(display.getElement());
            for (const helper of [mouse, touch]) helper.onEach(["mousedown", "mouseup", "mousemove"], sendMouse);
            display.onresize = () => {
                if (display.getWidth()) display.scale(Math.min(1, screen.clientWidth / display.getWidth()));
            };
            current.onerror = tunnel.onerror = () => {
                if (client === current) failure = "Console connection failed. Check the Unix password and service readiness, then reconnect.";
            };
            const ended = () => {
                if (client !== current) return;
                reset(); status.textContent = failure || "Console disconnected. Connect again for a new shell.";
            };
            tunnel.onstatechange = state => { if (state === Guacamole.Tunnel.State.CLOSED) ended(); };
            current.onstatechange = state => {
                if (client !== current) return;
                if (state === 3) {
                    connected = true; controls(); resize();
                    status.textContent = "Connected. Click the console to type.";
                    view.scrollIntoView({block: "start", behavior: "smooth"});
                    screen.focus({preventScroll: true}); poll();
                } else if (state === 5) ended();
            };
            current.connect(`attachment=${encodeURIComponent(attachment)}`);
        } catch (error) { reset(); status.textContent = error.message; }
        finally { busy = false; controls(); }
    });
    keyboard.onkeydown = key => { if (client && connected) { client.sendKeyEvent(1, key); return false; } };
    keyboard.onkeyup = key => { if (client && connected) client.sendKeyEvent(0, key); };
    function tap(key) {
        if (!client || !connected) return;
        client.sendKeyEvent(1, key); client.sendKeyEvent(0, key);
    }
    function sendText() {
        const value = text.value; text.value = "";
        for (const character of value) {
            const code = character.codePointAt(0);
            tap(code === 10 ? 0xFF0D : code <= 0xFF ? code : 0x01000000 | code);
        }
    }
    text.addEventListener("input", event => { if (!event.isComposing) sendText(); });
    text.addEventListener("compositionend", sendText);
    text.addEventListener("beforeinput", event => {
        if (event.inputType === "deleteContentBackward" && !event.isComposing) { event.preventDefault(); tap(0xFF08); }
    });
    text.addEventListener("keydown", event => {
        const key = {Enter: 0xFF0D, Tab: 0xFF09, Escape: 0xFF1B,
            ArrowLeft: 0xFF51, ArrowUp: 0xFF52, ArrowRight: 0xFF53, ArrowDown: 0xFF54}[event.key];
        if (key && !event.isComposing) { event.preventDefault(); tap(key); }
    });
    for (const button of buttons) {
        button.addEventListener("pointerdown", event => event.preventDefault());
        button.addEventListener("click", () => {
            if (!client || !connected) return;
            if (button.dataset.modifier) {
                const key = Number(button.dataset.modifier), held = !modifiers.has(key);
                if (held) modifiers.add(key); else modifiers.delete(key);
                client.sendKeyEvent(held ? 1 : 0, key); button.setAttribute("aria-pressed", String(held));
            } else tap(Number(button.dataset.key));
        });
    }
    byId("keyboard-toggle").addEventListener("click", () => {
        const panel = byId("keyboard-panel"); panel.hidden = !panel.hidden;
        byId("keyboard-toggle").setAttribute("aria-expanded", String(!panel.hidden));
        if (!panel.hidden) text.focus(); else { releaseKeys(); screen.focus({preventScroll: true}); }
    });
    byId("disconnect").addEventListener("click", async () => {
        if (busy) return;
        busy = true; controls();
        const ticket = attachment;
        reset();
        try { await post("disconnect", {session: ticket}); status.textContent = "Disconnected. This shell has closed."; }
        catch (error) { status.textContent = error.message; }
        finally { busy = false; controls(); }
    });
    byId("fullscreen").addEventListener("click", () => {
        if (document.fullscreenElement === view) document.exitFullscreen().catch(() => {});
        else if (view.requestFullscreen) view.requestFullscreen().catch(() => { status.textContent = "Fullscreen unavailable. Rotate your device for a wider console."; });
    });
    screen.addEventListener("click", () => { if (connected && byId("keyboard-panel").hidden) screen.focus({preventScroll: true}); });
    screen.addEventListener("blur", releaseKeys);
    window.addEventListener("blur", releaseKeys);
    view.addEventListener("focusout", event => { if (!view.contains(event.relatedTarget)) releaseKeys(); });
    document.addEventListener("visibilitychange", () => { if (document.hidden) releaseKeys(); });
    window.addEventListener("resize", resize);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", resize);
    document.addEventListener("fullscreenchange", () => {
        byId("fullscreen").textContent = document.fullscreenElement === view ? "Exit fullscreen" : "Fullscreen";
        resize();
    });
    window.addEventListener("pagehide", () => {
        active = false;
        const ticket = attachment;
        reset();
        if (ticket) post("disconnect", {session: ticket}, true).catch(() => {});
    });
    window.addEventListener("pageshow", () => { active = true; controls(); });
    controls();
})();
