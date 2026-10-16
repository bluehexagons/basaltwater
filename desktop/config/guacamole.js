/* Browser controls for the account-local Guacamole desktop. */
"use strict";
(() => {
    const byId = id => document.getElementById(id);
    const screen = byId("screen");
    const status = byId("status");
    const lifetime = byId("lifetime");
    const csrf = byId("csrf").value;
    const view = byId("desktop-view");
    const textInput = byId("desktop-keyboard");
    const modifierButtons = [...document.querySelectorAll("[data-modifier]")];
    const inputButtons = [...document.querySelectorAll("[data-key], [data-modifier]")];
    const modifiers = new Set();
    let client = null;
    let attachment = null;
    let controlled = false;
    let keyboard = null;
    let mouse = null;
    let busy = false;
    let connected = false;
    let displayElement = null;
    let pollTimer = null;
    let revision = 0;
    let connectionError = "";
    let pageActive = true;

    function controls(connected) {
        for (const id of ["take", "observe", "resume", "logout", "confirm-logout", "cancel-logout"])
            byId(id).disabled = !connected || busy;
        byId("take").disabled = !connected || busy || controlled;
        byId("take").setAttribute("aria-pressed", String(controlled));
        byId("take").textContent = controlled ? "You have control" : "Take control";
        byId("fullscreen").disabled = !connected;
        byId("disconnect").disabled = busy;
        byId("connect").disabled = connected || busy || client !== null;
        byId("password").disabled = connected || busy || client !== null;
        byId("keyboard-toggle").disabled = !connected || !controlled || busy;
        textInput.disabled = !connected || !controlled || busy;
        for (const button of inputButtons) button.disabled = !connected || !controlled || busy;
    }

    async function post(path, data = {}) {
        const response = await fetch(`/desktop/${path}`, {
            method: "POST", credentials: "same-origin", cache: "no-store",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({csrf, ...data})
        });
        if (!response.ok) throw new Error(await response.text());
        return response.json();
    }

    function releaseKeys() {
        if (keyboard) keyboard.reset();
        if (client) for (const key of modifiers) client.sendKeyEvent(0, key);
        modifiers.clear();
        for (const button of modifierButtons) button.setAttribute("aria-pressed", "false");
        textInput.value = "";
    }

    function observe() {
        releaseKeys();
        controlled = false;
    }

    function reset() {
        observe();
        const previous = client;
        client = null;
        connected = false;
        attachment = null;
        displayElement = null;
        byId("keyboard-panel").hidden = true;
        byId("keyboard-toggle").setAttribute("aria-expanded", "false");
        byId("logout-confirmation").hidden = true;
        revision++;
        clearTimeout(pollTimer);
        pollTimer = null;
        lifetime.textContent = "Connections last at most one hour and expire after ten minutes without input or a control action.";
        if (previous) previous.disconnect();
        // Reuse input listeners between connections, changing their client.
        screen.replaceChildren();
        controls(false);
    }

    function showState(result) {
        if (!result.controlled) observe();
        controlled = result.controlled;
        controls(connected);
        status.textContent = controlled ? "You control the desktop. Agent desktop actions are paused."
            : result.paused ? "Observing only. Agent desktop actions remain paused."
            : "Observing only. Agent desktop actions are enabled.";
        lifetime.textContent = `Session expires in ${Math.ceil(result.expires_in / 60)} min; inactivity expires in ${Math.ceil(result.idle_expires_in / 60)} min. Select Observe only to keep observing.`;
    }

    function scheduleStatus() {
        clearTimeout(pollTimer);
        if (connected) pollTimer = setTimeout(refreshStatus, 3000);
    }

    async function refreshStatus() {
        const current = client;
        const ticket = attachment;
        const observedRevision = revision;
        try {
            if (!busy && connected) {
                const result = await post("status", {session: ticket});
                if (client === current && revision === observedRevision) showState(result);
            }
        } catch (error) {
            if (client === current && revision === observedRevision) {
                reset();
                status.textContent = error.message;
            }
        } finally { scheduleStatus(); }
    }

    function scale() {
        if (!client) return;
        const display = client.getDisplay();
        if (!display.getWidth()) return;
        const size = byId("zoom").value;
        let factor = Number(size);
        if (size === "fit") {
            factor = Math.min(1, screen.clientWidth / display.getWidth());
            if (document.fullscreenElement === view)
                factor = Math.min(factor, screen.clientHeight / display.getHeight());
        }
        display.scale(factor);
    }

    function sendMouse(event, relativeToDisplay = false) {
        if (!client || !controlled || byId("touch-mode").value === "pan") return;
        if (byId("keyboard-panel").hidden) screen.focus({preventScroll: true});
        const display = client.getDisplay();
        const screenBounds = screen.getBoundingClientRect();
        const displayBounds = displayElement.getBoundingClientRect();
        // Translate container mouse coordinates to the display origin,
        // including scrolling. Touch helpers already use display coordinates.
        const state = {...event.state};
        if (!relativeToDisplay) {
            state.x -= displayBounds.left - screenBounds.left + screen.scrollLeft;
            state.y -= displayBounds.top - screenBounds.top + screen.scrollTop;
        }
        // Drag release events may lie outside the scaled display. Clamp their
        // position while preserving button releases, so buttons cannot stick.
        state.x = Math.max(0, Math.min(state.x, (display.getWidth() - 1) * display.getScale()));
        state.y = Math.max(0, Math.min(state.y, (display.getHeight() - 1) * display.getScale()));
        client.sendMouseState(state, true);
    }

    byId("connect-form").addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || client) return;
        busy = true;
        const attempt = ++revision;
        connectionError = "";
        controls(false);
        status.textContent = "Connecting to the shared desktop…";
        try {
            let password = byId("password").value;
            byId("password").value = "";
            const pending = post("session", {password});
            password = "";
            const result = await pending;
            if (!pageActive || revision !== attempt) {
                await post("disconnect", {session: result.attachment}).catch(() => {});
                return;
            }
            attachment = result.attachment;
            const tunnel = new Guacamole.WebSocketTunnel("/desktop/ws");
            const current = new Guacamole.Client(tunnel);
            client = current;
            const display = client.getDisplay();
            // Guacamole 1.6's ImageDecoder path retains VideoFrames/decoders
            // and leaves the display queue blocked when decoding rejects.
            // Use its existing Image/data-URI path, whose load/error handlers
            // always release the rendering queue, for this client instance.
            display.drawStream = (layer, x, y, stream, mimetype) => {
                const reader = new Guacamole.DataURIReader(stream, mimetype);
                reader.onend = () => display.draw(layer, x, y, reader.getURI());
            };
            displayElement = display.getElement();
            screen.appendChild(displayElement);
            const directTouch = new Guacamole.Mouse.Touchscreen(displayElement);
            const trackpad = new Guacamole.Mouse.Touchpad(displayElement);
            for (const [helper, mode] of [[directTouch, "direct"], [trackpad, "trackpad"]])
                helper.onEach(["mousedown", "mouseup", "mousemove"], event => {
                    if (byId("touch-mode").value === mode) sendMouse(event, true);
                });
            client.getDisplay().onresize = scale;
            client.onerror = tunnel.onerror = () => {
                if (client !== current) return;
                connectionError = "Desktop connection failed. Check the Unix password and desktop readiness, then reconnect.";
                status.textContent = connectionError;
            };
            tunnel.onstatechange = state => {
                if (client !== current || state !== Guacamole.Tunnel.State.CLOSED) return;
                reset();
                status.textContent = connectionError || "Desktop connection ended. Browser control released. Connect again to start or rejoin the desktop.";
            };
            client.onstatechange = state => {
                if (client !== current) return;
                if (state === 3) {
                    connected = true;
                    status.textContent = "Observing only. Select Take control to pause agent desktop actions.";
                    controls(true);
                    scale();
                    scheduleStatus();
                } else if (state === 5) {
                    reset();
                    status.textContent = connectionError || "Desktop connection ended. Browser control released. Connect again to start or rejoin the desktop.";
                }
            };
            // Input helpers attach once to the persistent screen container.
            if (!mouse) {
                mouse = new Guacamole.Mouse(screen);
                mouse.onEach(["mousedown", "mouseup", "mousemove"], sendMouse);
                keyboard = new Guacamole.Keyboard(screen);
                keyboard.onkeydown = keysym => {
                    if (client && controlled) { client.sendKeyEvent(1, keysym); return false; }
                };
                keyboard.onkeyup = keysym => { if (client && controlled) client.sendKeyEvent(0, keysym); };
            }
            client.connect(`attachment=${encodeURIComponent(attachment)}`);
        } catch (error) {
            reset();
            status.textContent = error.message;
        } finally {
            busy = false;
            controls(connected);
        }
    });

    for (const action of ["take", "observe", "resume"]) {
        byId(action).addEventListener("click", async () => {
            if (!attachment || !connected || busy) return;
            const current = client;
            const ticket = attachment;
            revision++;
            observe();
            busy = true;
            controls(connected);
            try {
                const result = await post("control", {session: ticket, action});
                if (client !== current || attachment !== ticket) return;
                showState(result);
                if (controlled) screen.focus({preventScroll: true});
            } catch (error) {
                if (client === current && attachment === ticket) status.textContent = error.message;
            } finally {
                busy = false;
                controls(connected);
            }
        });
    }
    byId("disconnect").addEventListener("click", async () => {
        if (busy) return;
        revision++;
        observe();
        busy = true;
        controls(connected);
        try {
            await post("disconnect", {session: attachment});
            reset();
            status.textContent = "Disconnected. The desktop remains running. Browser control released; any separate agent pause is preserved.";
        } catch (error) { status.textContent = error.message; }
        finally { busy = false; controls(connected); }
    });
    byId("logout").addEventListener("click", () => {
        if (!connected || busy) return;
        releaseKeys();
        byId("logout-confirmation").hidden = false;
        byId("confirm-logout").focus();
    });
    byId("cancel-logout").addEventListener("click", () => {
        byId("logout-confirmation").hidden = true;
        byId("logout").focus();
    });
    byId("confirm-logout").addEventListener("click", async () => {
        if (!attachment || !connected || busy) return;
        const current = client;
        const ticket = attachment;
        revision++;
        observe();
        busy = true;
        byId("logout-confirmation").hidden = true;
        controls(connected);
        try {
            const result = await post("logout", {session: ticket});
            if (client !== current || attachment !== ticket) return;
            showState(result);
            status.textContent = "Desktop logout requested. Resolve any unsaved-work dialogs in the desktop. The connection closes when logout finishes.";
            if (controlled) screen.focus({preventScroll: true});
        } catch (error) {
            if (client === current && attachment === ticket) status.textContent = error.message;
        } finally { busy = false; controls(connected); }
    });
    byId("fullscreen").addEventListener("click", () => {
        if (document.fullscreenElement === view) document.exitFullscreen().catch(() => {});
        else if (view.requestFullscreen)
            view.requestFullscreen().catch(() => { status.textContent = "Fullscreen is unavailable in this browser. Rotate your device for a wider view."; });
        else status.textContent = "Fullscreen is unavailable in this browser. Rotate your device for a wider view.";
    });
    byId("zoom").addEventListener("change", scale);
    byId("display-options").addEventListener("toggle", scale);
    byId("touch-mode").addEventListener("change", () => {
        if (client && controlled) client.sendMouseState({x: 0, y: 0, left: false, middle: false, right: false, up: false, down: false});
        screen.classList.toggle("pan-display", byId("touch-mode").value === "pan");
    });
    function tapKey(key) {
        if (!client || !controlled || busy) return;
        client.sendKeyEvent(1, key);
        client.sendKeyEvent(0, key);
    }
    function sendText() {
        const value = textInput.value;
        textInput.value = "";
        for (const character of value) {
            const code = character.codePointAt(0);
            tapKey(code === 10 ? 0xFF0D : code <= 0xFF ? code : 0x01000000 | code);
        }
    }
    byId("keyboard-toggle").addEventListener("click", () => {
        const panel = byId("keyboard-panel");
        panel.hidden = !panel.hidden;
        byId("keyboard-toggle").setAttribute("aria-expanded", String(!panel.hidden));
        if (!panel.hidden) textInput.focus();
        else { releaseKeys(); screen.focus({preventScroll: true}); }
        scale();
    });
    textInput.addEventListener("input", event => { if (!event.isComposing) sendText(); });
    textInput.addEventListener("compositionend", sendText);
    textInput.addEventListener("beforeinput", event => {
        if (event.inputType === "deleteContentBackward" && !event.isComposing) {
            event.preventDefault();
            tapKey(0xFF08);
        }
    });
    textInput.addEventListener("keydown", event => {
        const key = {Enter: 0xFF0D, Tab: 0xFF09, Escape: 0xFF1B,
            ArrowLeft: 0xFF51, ArrowUp: 0xFF52, ArrowRight: 0xFF53, ArrowDown: 0xFF54}[event.key];
        if (key && !event.isComposing) { event.preventDefault(); tapKey(key); }
    });
    for (const target of [screen, textInput]) target.addEventListener("keydown", event => {
        if (!connected || busy || !event.ctrlKey || !event.altKey || !event.shiftKey) return;
        // Capture before Guacamole consumes Tab and other remote keys.
        event.preventDefault();
        event.stopImmediatePropagation();
        releaseKeys();
        byId("keyboard-toggle").focus();
    }, {capture: true});
    for (const button of inputButtons) {
        // Keep the phone keyboard open when using the extra keys.
        button.addEventListener("pointerdown", event => event.preventDefault());
        button.addEventListener("click", () => {
            if (!client || !controlled || busy) return;
            if (button.dataset.modifier) {
                const key = Number(button.dataset.modifier);
                const held = !modifiers.has(key);
                if (held) modifiers.add(key); else modifiers.delete(key);
                client.sendKeyEvent(held ? 1 : 0, key);
                button.setAttribute("aria-pressed", String(held));
            } else tapKey(Number(button.dataset.key));
        });
    }
    byId("inspect").addEventListener("click", async () => {
        byId("inspect").disabled = true;
        try {
            const result = await post("inspect");
            byId("desktop-status").textContent = `Desktop: ${result.state}${result.geometry ? ` · ${result.geometry.join(" × ")}` : ""}${result.state === "running" ? result.paused ? " · agent paused" : " · agent enabled" : ""}.`;
        } catch (error) { byId("desktop-status").textContent = error.message; }
        finally { byId("inspect").disabled = false; }
    });
    byId("copy-ssh").addEventListener("click", async () => {
        const command = byId("ssh-command");
        try {
            await navigator.clipboard.writeText(command.value);
            byId("ssh-status").textContent = "SSH command copied. Paste it into your terminal.";
        } catch (_) {
            command.focus(); command.select();
            byId("ssh-status").textContent = "Select and copy the SSH command from the field above.";
        }
    });
    screen.addEventListener("blur", releaseKeys);
    screen.addEventListener("wheel", event => {
        // Guacamole consumes wheel events even when input is disabled. Let
        // the browser scroll the zoomed display in pan/observation mode.
        if (!controlled || byId("touch-mode").value === "pan") event.stopImmediatePropagation();
    }, {capture: true});
    view.addEventListener("focusout", event => {
        if (!view.contains(event.relatedTarget)) releaseKeys();
    });
    window.addEventListener("blur", releaseKeys);
    document.addEventListener("visibilitychange", () => { if (document.hidden) releaseKeys(); });
    window.addEventListener("resize", scale);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", scale);
    document.addEventListener("fullscreenchange", () => {
        byId("fullscreen").textContent = document.fullscreenElement === view ? "Exit fullscreen" : "Fullscreen";
        scale();
    });
    window.addEventListener("pagehide", () => {
        pageActive = false;
        const ticket = attachment;
        reset();
        status.textContent = "Disconnected. Browser control released. Connect again to rejoin the desktop.";
        // Also revoke a pending attachment before its WebSocket is ready.
        // The server handles dropped sockets and lease expiry independently.
        if (ticket) fetch("/desktop/disconnect", {
            method: "POST", credentials: "same-origin", cache: "no-store", keepalive: true,
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({csrf, session: ticket})
        }).catch(() => {});
    });
    window.addEventListener("pageshow", () => { pageActive = true; controls(connected); });
})();
