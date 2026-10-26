# Prebuilt runtime validation

`lib.build_runtime.validate_prebuilt_runtime()` is an internal prerequisite for
the first shared-builder slice. It is not wired to a command, deployment path,
or general runtime installer yet. A future caller must invoke it on a private,
verified extracted release tree before activation.

The helper currently accepts only the `linux` / `x86_64` / `debian-13`
platform. Static components have no executable runtime requirement. Service
components must identify a declared, executable regular file through
`binary`, or through the first `exec` argument in the exact form
`{{release_dir}}/relative/path`. Exec-only services still need explicit
manifest `artifacts`; the executable must be covered by the selected exports.
The helper does not rewrite `exec`, infer additional files from it, or run
artifact commands.

Service executables must be ELF64 little-endian x86-64 `ET_EXEC` or `ET_DYN`
files with `EI_OSABI` 0 (System V) or 3 (Linux) and `EI_ABIVERSION` 0. They
must have bounded, valid program headers, an executable load segment covering
the entry point, no `PT_INTERP`, and no `DT_NEEDED` entries in a valid bounded
`PT_DYNAMIC` segment. Access uses no-follow directory descriptors and checks
file identity before and after metadata reads. It does not execute the file or
call host inspection tools.

Godot components, other platforms, dynamic runtimes such as Node.js or Python,
and runtime-package installation are deferred; Node/Python runtime packaging
has not been qualified. ELF validation is deliberately conservative and may
reject valid but unusual ELF layouts; it does not identify Go provenance or
claim compatibility with every static ELF variant. A caller still must
authenticate the request, apply target admission and authorization, verify the
artifact descriptor and payload, and provide the private extracted tree. This
helper establishes only the narrow runtime compatibility check.
