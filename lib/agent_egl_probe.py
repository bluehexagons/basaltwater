"""Isolated EGL/OpenGL pbuffer probe; invoked only by the graphics doctor."""

from __future__ import annotations

import ctypes as C
import json
import sys


class EGLFailure(RuntimeError):
    """A stable diagnostic step failed without exposing driver output."""


def probe_context(platform: str) -> dict:
    """Create a 1x1 offscreen OpenGL context and query its actual renderer."""
    if platform not in ("offscreen", "surfaceless"):
        raise ValueError("Choose offscreen or surfaceless")
    egl = C.CDLL("libEGL.so.1")
    pointer, integer, boolean = C.c_void_p, C.c_int, C.c_uint

    def bind(name, result, arguments):
        function = getattr(egl, name)
        function.restype = result
        function.argtypes = arguments
        return function

    get_display = bind("eglGetDisplay", pointer, [pointer])
    initialize = bind("eglInitialize", boolean, [pointer, C.POINTER(integer), C.POINTER(integer)])
    choose_config = bind("eglChooseConfig", boolean, [pointer, C.POINTER(integer), C.POINTER(pointer), integer, C.POINTER(integer)])
    bind_api = bind("eglBindAPI", boolean, [C.c_uint])
    create_surface = bind("eglCreatePbufferSurface", pointer, [pointer, pointer, C.POINTER(integer)])
    create_context = bind("eglCreateContext", pointer, [pointer, pointer, pointer, C.POINTER(integer)])
    make_current = bind("eglMakeCurrent", boolean, [pointer, pointer, pointer, pointer])
    query_string = bind("eglQueryString", C.c_char_p, [pointer, integer])
    get_proc = bind("eglGetProcAddress", pointer, [C.c_char_p])
    destroy_context = bind("eglDestroyContext", boolean, [pointer, pointer])
    destroy_surface = bind("eglDestroySurface", boolean, [pointer, pointer])
    terminate = bind("eglTerminate", boolean, [pointer])
    if platform == "surfaceless":
        extensions = (query_string(None, 0x3055) or b"").split()
        if b"EGL_MESA_platform_surfaceless" not in extensions:
            raise EGLFailure("egl_surfaceless_unsupported")
        if b"EGL_EXT_platform_base" in extensions:
            address = get_proc(b"eglGetPlatformDisplayEXT")
            if not address:
                raise EGLFailure("egl_platform_entrypoint_unavailable")
            get_platform = C.CFUNCTYPE(pointer, C.c_uint, pointer, C.POINTER(integer))(address)
        else:
            # EGL 1.5 provides the core entrypoint without EXT_platform_base.
            try:
                get_platform = bind("eglGetPlatformDisplay", pointer, [C.c_uint, pointer, C.POINTER(C.c_ssize_t)])
            except AttributeError as exc:
                raise EGLFailure("egl_platform_entrypoint_unavailable") from exc
        display = get_platform(0x31DD, None, None)
    else:
        # Uses the implementation's default display and inherited EGL_PLATFORM.
        # Do not substitute surfaceless or another GPU after a failure.
        display = get_display(None)
    if not display:
        raise EGLFailure("egl_display_unavailable")
    major, minor = integer(), integer()
    if not initialize(display, C.byref(major), C.byref(minor)):
        raise EGLFailure("egl_initialize_failed")
    surface = context = None
    try:
        if not bind_api(0x30A2):  # EGL_OPENGL_API
            raise EGLFailure("egl_opengl_api_unavailable")
        config, count = pointer(), integer()
        attributes = (integer * 11)(0x3033, 0x0001, 0x3040, 0x0008,  # PBUFFER_BIT, OPENGL_BIT
                                    0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3038)
        if not choose_config(display, attributes, C.byref(config), 1, C.byref(count)) or count.value < 1:
            raise EGLFailure("egl_pbuffer_config_unavailable")
        surface = create_surface(display, config, (integer * 5)(0x3057, 1, 0x3056, 1, 0x3038))
        if not surface:
            raise EGLFailure("egl_pbuffer_creation_failed")
        context = create_context(display, config, None, (integer * 1)(0x3038))
        if not context:
            raise EGLFailure("egl_context_creation_failed")
        if not make_current(display, surface, surface, context):
            raise EGLFailure("egl_make_current_failed")
        address = get_proc(b"glGetString")
        if not address:
            raise EGLFailure("egl_gl_query_unavailable")
        get_string = C.CFUNCTYPE(C.c_char_p, C.c_uint)(address)

        def text(value):
            return value.decode("utf-8", errors="replace")[:256] if value else None

        return {"renderer": text(get_string(0x1F01)), "opengl_version": text(get_string(0x1F02)),
                "egl_vendor": text(query_string(display, 0x3053)),
                "egl_version": f"{major.value}.{minor.value}", "client_api": "OpenGL"}
    finally:
        make_current(display, None, None, None)
        if context:
            destroy_context(display, context)
        if surface:
            destroy_surface(display, surface)
        terminate(display)


def main() -> None:
    try:
        result = probe_context(sys.argv[1])
    except EGLFailure as exc:
        result = {"issue": str(exc)}
    except (OSError, AttributeError):
        result = {"issue": "egl_library_unavailable"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
