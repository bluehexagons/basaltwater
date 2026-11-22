"""Unix identity policy for Basaltwater-managed Samba logins."""

from __future__ import annotations

import grp
import os
import pwd


# Debian administrative and privileged device/service groups. Group 0 is
# checked separately to cover aliases of the root group.
PRIVILEGED_GROUPS = ("root", "sudo", "wheel", "admin", "adm", "disk", "shadow",
                     "docker", "lxd", "incus", "incus-admin", "libvirt")


def is_share_identity(account: pwd.struct_passwd) -> bool:
    if not 1000 <= account.pw_uid < 65534:
        return False
    privileged = {0}
    for name in PRIVILEGED_GROUPS:
        try:
            privileged.add(grp.getgrnam(name).gr_gid)
        except KeyError:
            pass
    return privileged.isdisjoint(os.getgrouplist(account.pw_name, account.pw_gid))
