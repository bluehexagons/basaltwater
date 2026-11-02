"""Bounded, read-only KDE/Fcitx input metadata; never activate bus services."""

from __future__ import annotations

import json
import unicodedata


def _label(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= 128
            and not any(unicodedata.category(char).startswith("C") for char in value))


def collect_input_environment(probe, *, bus_ready: bool) -> dict[str, object]:
    """Use the caller's bounded local-bus probe, without files or project tools.

    Null means unknown. Bus ownership is discovery, not proof of a working IME.
    Enabled engines describe only the current Fcitx group, never all installed
    engines. Layout and input method can change immediately after observation.
    """
    result = {
        "keyboard": {"state": "unknown", "layouts": None, "active_index": None},
        "frameworks": {
            "fcitx5": {"owner_observed": None, "current_engine": None,
                       "current_group": None, "enabled_engines": None},
            "ibus": {"owner_observed": None},
        },
        "scope": "KDE bus layout and Fcitx current-group snapshot; not per-application IME qualification or a focus guard.",
    }
    if not bus_ready:
        return result

    def call(destination, path, interface, method, signature, *arguments):
        status, output = probe([
            "/usr/bin/busctl", "--user", "--timeout=2", "--auto-start=no",
            "--json=short", "call", destination, path, interface, method, *arguments,
        ])
        if status != "ok" or len(output.encode("utf-8")) > 16384:
            return None
        try:
            response = json.loads(output)
        except (ValueError, RecursionError):
            return None
        if not isinstance(response, dict) or response.get("type") != signature:
            return None
        data = response.get("data")
        return data if isinstance(data, list) else None

    def owned(name):
        data = call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "NameHasOwner", "b", "s", name)
        return data[0] if data is not None and len(data) == 1 and type(data[0]) is bool else None

    if owned("org.kde.keyboard"):
        def layout_call(method, signature):
            return call("org.kde.keyboard", "/Layouts", "org.kde.KeyboardLayouts", method, signature)

        layouts = layout_call("getLayoutsList", "a(sss)")
        index = layout_call("getLayout", "u")
        confirmed = layout_call("getLayoutsList", "a(sss)")
        if (layouts is not None and len(layouts) == 1 and layouts == confirmed
                and isinstance(layouts[0], list) and 1 <= len(layouts[0]) <= 32
                and all(isinstance(row, list) and len(row) == 3 and all(_label(v) for v in row)
                        for row in layouts[0])):
            result["keyboard"]["layouts"] = [
                dict(zip(("short_name", "display_name", "long_name"), row)) for row in layouts[0]
            ]
            if (index is not None and len(index) == 1 and type(index[0]) is int
                    and 0 <= index[0] < len(layouts[0])):
                result["keyboard"].update(state="observed", active_index=index[0])

    for name, destination in (("fcitx5", "org.fcitx.Fcitx5"), ("ibus", "org.freedesktop.IBus")):
        result["frameworks"][name]["owner_observed"] = owned(destination)
    fcitx = result["frameworks"]["fcitx5"]
    if fcitx["owner_observed"]:
        def fcitx_call(method, signature, *arguments):
            return call("org.fcitx.Fcitx5", "/controller", "org.fcitx.Fcitx.Controller1",
                        method, signature, *arguments)

        group = fcitx_call("CurrentInputMethodGroup", "s")
        engine = fcitx_call("CurrentInputMethod", "s")
        if group is not None and len(group) == 1 and _label(group[0]) and group[0]:
            info = fcitx_call("InputMethodGroupInfo", "sa(ss)", "s", group[0])
            confirmed = fcitx_call("CurrentInputMethodGroup", "s")
            if group == confirmed:
                fcitx["current_group"] = group[0]
                if engine is not None and len(engine) == 1 and _label(engine[0]) and engine[0]:
                    fcitx["current_engine"] = engine[0]
                if (info is not None and len(info) == 2 and _label(info[0])
                        and isinstance(info[1], list) and len(info[1]) <= 64
                        and all(isinstance(row, list) and len(row) == 2
                                and all(_label(v) for v in row) and row[0] for row in info[1])):
                    fcitx["enabled_engines"] = [
                        {"name": row[0], "layout": row[1]} for row in info[1]
                    ]
    return result
