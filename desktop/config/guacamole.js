/* Browser controls for the account-local Guacamole desktop. */
"use strict";
(() => {
    const byId = id => document.getElementById(id);
    const screen = byId("screen");
    const status = byId("status");
    const lifetime = byId("lifetime");
    const csrf = byId("csrf").value;
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

    function controls(connected) {
        for (const id of ["take", "observe", "resume"])
            byId(id).disabled = !connected || busy;
        byId("fullscreen").disabled = !connected;
        byId("disconnect").disabled = busy;
        byId("connect").disabled = connected || busy || client !== null;
        byId("password").disabled = connected || busy || client !== null;
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
        if (display.getWidth()) display.scale(Math.min(1, screen.clientWidth / display.getWidth()));
    }

    function sendMouse(event) {
        if (!client || !controlled) return;
        screen.focus({preventScroll: true});
        const display = client.getDisplay();
        const screenBounds = screen.getBoundingClientRect();
        const displayBounds = displayElement.getBoundingClientRect();
        // Fullscreen centers the display within the input container. Translate
        // its mouse coordinates to the display origin, including scrolling.
        const state = {...event.state,
            x: event.state.x - (displayBounds.left - screenBounds.left + screen.scrollLeft),
            y: event.state.y - (displayBounds.top - screenBounds.top + screen.scrollTop)};
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
        revision++;
        connectionError = "";
        controls(false);
        status.textContent = "Connecting to the shared desktop…";
        try {
            let password = byId("password").value;
            byId("password").value = "";
            const pending = post("session", {password});
            password = "";
            const result = await pending;
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
            client.getDisplay().onresize = scale;
            client.onerror = tunnel.onerror = () => {
                if (client !== current) return;
                connectionError = "Desktop stream failed. Reconnect after checking desktop readiness.";
                status.textContent = connectionError;
            };
            tunnel.onstatechange = state => {
                if (client !== current || state !== Guacamole.Tunnel.State.CLOSED) return;
                reset();
                status.textContent = connectionError || "Desktop connection closed. Reconnect to the running desktop.";
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
                    status.textContent = connectionError || "Disconnected. The desktop remains running; agent pause is unchanged.";
                }
            };
            // Input helpers attach once to the persistent screen container.
            if (!mouse) {
                mouse = new Guacamole.Mouse(screen);
                mouse.onEach(["mousedown", "mouseup", "mousemove"], sendMouse);
                const touch = new Guacamole.Mouse.Touchscreen(screen);
                touch.onEach(["mousedown", "mouseup", "mousemove"], sendMouse);
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
            status.textContent = "Disconnected. The desktop remains running; agent pause is unchanged.";
        } catch (error) { status.textContent = error.message; }
        finally { busy = false; controls(connected); }
    });
    byId("fullscreen").addEventListener("click", () => {
        screen.requestFullscreen().catch(() => { status.textContent = "Fullscreen is unavailable in this browser."; });
    });
    screen.addEventListener("blur", releaseKeys);
    window.addEventListener("resize", scale);
    document.addEventListener("fullscreenchange", scale);
    window.addEventListener("pagehide", () => { if (client) client.disconnect(); });
})();
