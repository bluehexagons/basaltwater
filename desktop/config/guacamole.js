/* Browser controls for the account-local Guacamole desktop. */
"use strict";
(() => {
    const byId = id => document.getElementById(id);
    const screen = byId("screen");
    const status = byId("status");
    const csrf = byId("csrf").value;
    let client = null;
    let attachment = null;
    let controlled = false;
    let keyboard = null;
    let mouse = null;
    let busy = false;
    let connected = false;
    let displayElement = null;

    function controls(connected) {
        for (const id of ["take", "observe", "resume", "fullscreen"])
            byId(id).disabled = !connected;
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
        if (client) client.disconnect();
        client = null;
        connected = false;
        attachment = null;
        displayElement = null;
        // Reuse input listeners between connections, changing their client.
        screen.replaceChildren();
        controls(false);
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
        // Drag release events may lie outside the scaled display. Clamp their
        // position while preserving button releases, so buttons cannot stick.
        event.state.x = Math.max(0, Math.min(event.state.x, (display.getWidth() - 1) * display.getScale()));
        event.state.y = Math.max(0, Math.min(event.state.y, (display.getHeight() - 1) * display.getScale()));
        client.sendMouseState(event.state, true);
    }

    byId("connect-form").addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || client) return;
        busy = true;
        controls(false);
        status.textContent = "Connecting to the shared desktop…";
        try {
            let password = byId("password").value;
            byId("password").value = "";
            const pending = post("session", {password});
            password = "";
            const result = await pending;
            attachment = result.attachment;
            client = new Guacamole.Client(new Guacamole.WebSocketTunnel("/desktop/ws"));
            displayElement = client.getDisplay().getElement();
            screen.appendChild(displayElement);
            client.getDisplay().onresize = scale;
            client.onerror = () => { status.textContent = "Desktop connection failed. Reconnect after checking the Unix password and desktop readiness."; };
            client.onstatechange = state => {
                if (state === 3) {
                    connected = true;
                    status.textContent = "Observing only. Select Take control to pause agent desktop actions.";
                    controls(true);
                    scale();
                } else if (state === 5) {
                    reset();
                    status.textContent = "Disconnected. The desktop remains running; agent pause is unchanged.";
                }
            };
            // Input helpers attach once to the persistent screen container.
            if (!mouse) {
                mouse = new Guacamole.Mouse(screen);
                mouse.on(["mousedown", "mouseup", "mousemove"], sendMouse);
                const touch = new Guacamole.Mouse.Touchscreen(screen);
                touch.on(["mousedown", "mouseup", "mousemove"], sendMouse);
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
            if (!attachment) return;
            observe();
            try {
                const result = await post("control", {session: attachment, action});
                controlled = result.controlled;
                status.textContent = controlled ? "You control the desktop. Agent desktop actions are paused."
                    : result.paused ? "Observing only. Agent desktop actions remain paused."
                    : "Observing only. Agent desktop actions are enabled.";
                if (controlled) screen.focus({preventScroll: true});
            } catch (error) { status.textContent = error.message; }
        });
    }
    byId("disconnect").addEventListener("click", async () => {
        observe();
        try {
            await post("disconnect");
            reset();
            status.textContent = "Disconnected. The desktop remains running; agent pause is unchanged.";
        } catch (error) { status.textContent = error.message; }
    });
    byId("fullscreen").addEventListener("click", () => {
        screen.requestFullscreen().catch(() => { status.textContent = "Fullscreen is unavailable in this browser."; });
    });
    screen.addEventListener("blur", releaseKeys);
    window.addEventListener("resize", scale);
    document.addEventListener("fullscreenchange", scale);
    window.addEventListener("pagehide", () => { if (client) client.disconnect(); });
})();
