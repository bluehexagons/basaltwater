"""Compile audit permission filters using the installed audit userspace."""

from __future__ import annotations

import ctypes
from functools import cache


class _AuditRuleData(ctypes.Structure):
    # Prefix of struct audit_rule_data from linux/audit.h. Libaudit allocates
    # the full structure, including its variable-length field data.
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("action", ctypes.c_uint32),
        ("field_count", ctypes.c_uint32),
        ("mask", ctypes.c_uint32 * 64),
    ]


@cache
def audit_permission_syscalls(arch: str, permissions: str) -> frozenset[str]:
    """Return the native syscall mask auditctl will print for a permission rule."""
    library = ctypes.CDLL("libaudit.so.1")
    pointer = ctypes.POINTER(_AuditRuleData)
    library.audit_rule_create_data.restype = pointer
    library.audit_rule_free_data.argtypes = [pointer]
    library.audit_rule_free_data.restype = None
    library.audit_rule_fieldpair_data.argtypes = [ctypes.POINTER(pointer), ctypes.c_char_p, ctypes.c_int]
    library.audit_determine_machine.argtypes = [ctypes.c_char_p]
    library.audit_syscall_to_name.argtypes = [ctypes.c_int, ctypes.c_int]
    library.audit_syscall_to_name.restype = ctypes.c_char_p

    # auditctl resets this parser state between rules. Restore it after this
    # read-only compilation so other libaudit callers in the process keep it.
    parser_state = [ctypes.c_int.in_dll(library, symbol) for symbol in (
        "_audit_elf", "_audit_archadded", "_audit_syscalladded", "_audit_permadded",
    )]
    previous_state = [field.value for field in parser_state]
    rule = library.audit_rule_create_data()
    if not rule:
        raise RuntimeError("Could not allocate an audit rule for verification")
    try:
        for field in parser_state:
            field.value = 0
        for expression in (f"arch={arch}", f"perm={permissions}"):
            field_pair = ctypes.create_string_buffer(expression.encode("ascii"))
            # AUDIT_FILTER_EXIT is the audit rule list for syscall filters.
            if library.audit_rule_fieldpair_data(ctypes.byref(rule), field_pair, 4) != 0:
                raise RuntimeError(f"Could not compile audit field {expression}")
        machine = library.audit_determine_machine(arch.encode("ascii"))
        syscalls = set()
        for index, word in enumerate(rule.contents.mask):
            for bit in range(32):
                if word & (1 << bit):
                    number = 32 * index + bit
                    name = library.audit_syscall_to_name(number, machine)
                    syscalls.add(name.decode("ascii") if name else str(number))
        return frozenset(syscalls)
    finally:
        library.audit_rule_free_data(rule)
        for field, value in zip(parser_state, previous_state, strict=True):
            field.value = value
