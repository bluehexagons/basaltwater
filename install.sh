#!/bin/sh

set -eu

REPOSITORY="bluehexagons/basaltwater"
# Explicit values, including empty settings, are validated below.
REPOSITORY_URL="${BASALTWATER_REPOSITORY_URL-https://github.com/$REPOSITORY.git}"
CHANNEL="${BASALTWATER_CHANNEL-dev}"
CHANNEL_SET=0
if [ "${BASALTWATER_CHANNEL+x}" = "x" ]; then
    CHANNEL_SET=1
fi
INSTALL_DIR=""
TARGET_USER=""
SHELL_NAME=""
RUN_SETUP=0
LOCAL_SETUP_REQUESTED=0
INSTALL_QEMU_GUEST_AGENT=0
HOST_OS_SUPPORTED=1
HOST_OS_ID=""

usage() {
    cat <<'EOF'
Install or update Basaltwater from a managed Git worktree.

Usage:
  install.sh [options]
  install.sh [options] --setup SYSTEM_TYPE HOST [USERNAME] [SETUP_OPTIONS...]
  install.sh [options] --local-setup SYSTEM_TYPE [SETUP_OPTIONS...]

Options:
  --channel CHANNEL     stable, dev, v[version], branch-[branch], or commit-[hash]
                        (default: dev for the current development release)
  --ref REF             Compatibility alias for --channel; bare refs are branches
  --install-dir PATH   Source destination (default: /opt/basaltwater as root,
                       otherwise ~/.local/share/basaltwater)
  --user USER          User receiving local tools and completions
  --shell SHELL        bash, zsh, fish, or tcsh (default: target user's shell)
  --qemu-guest-agent   Install, start, and enable Proxmox's qemu-guest-agent
                       during self-setup (must appear before --setup/--local-setup)
  --setup ...          Run `basaltw setup ...` after installation
  --local-setup TYPE   Run `basaltw setup TYPE localhost USER` after installation
                       (the target user comes from --user or the invoking user)
  -h, --help           Show this help

Examples:
  Download with wget, then run the script (run each line in order):
  wget --timeout=20 --tries=2 -O "$HOME/.basaltwater-install.sh" https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh
  sh "$HOME/.basaltwater-install.sh"
  rm -f "$HOME/.basaltwater-install.sh"
  Download with wget and run a privileged setup:
  wget --timeout=20 --tries=2 -O "$HOME/.basaltwater-install.sh" https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh
  sudo sh "$HOME/.basaltwater-install.sh" --user "$USER" --local-setup control_plane \
    --agent-tool gh --agent-tool codex --agent-tool claude --agent-tool opencode
  rm -f "$HOME/.basaltwater-install.sh"
  Add qemu-guest-agent when the orchestration host is a Proxmox VM by placing
  --qemu-guest-agent before --local-setup:
  sudo sh "$HOME/.basaltwater-install.sh" --user "$USER" --qemu-guest-agent --local-setup control_plane
  rm -f "$HOME/.basaltwater-install.sh"
  Download with curl instead by replacing the wget command with:
  curl --fail --location --connect-timeout 15 --max-time 120 -o "$HOME/.basaltwater-install.sh" https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh
EOF
}

fail() {
    printf 'Basaltwater installer: %s\n' "$*" >&2
    exit 1
}

confirm_unsupported_host() {
    printf '%s\n' "Warning: unsupported host distribution detected: ${HOST_OS_ID:-unknown}."
    printf '%s\n' "Debian is officially supported; Ubuntu and Linux Mint are best-effort."
    printf '%s' "Continue installing the remote-management tools anyway? [y/N] "
    response=""
    if [ -t 0 ] && [ -r /dev/tty ]; then
        IFS= read -r response < /dev/tty || true
    else
        IFS= read -r response || true
    fi
    case "$response" in
        y|Y|yes|Yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

run_privileged() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
        return
    fi

    command -v sudo >/dev/null 2>&1 || fail "this step requires root privileges; install sudo or rerun the installer with sudo"
    sudo "$@"
}

run_with_progress() {
    "$@" &
    command_pid=$!
    while kill -0 "$command_pid" 2>/dev/null; do
        sleep 10
        if kill -0 "$command_pid" 2>/dev/null; then
            printf '%s\n' "  Package operation is still running; waiting for APT or the network..."
        fi
    done
    wait "$command_pid"
}

run_bootstrap() {
    bootstrap_has_skip=0
    for bootstrap_arg in "$@"; do
        if [ "$bootstrap_arg" = "--skip-system-packages" ]; then
            bootstrap_has_skip=1
            break
        fi
    done
    if [ "$HOST_OS_SUPPORTED" -eq 0 ] && [ "$bootstrap_has_skip" -eq 0 ]; then
        set -- "$@" --skip-system-packages
    fi
    if [ "$INSTALL_QEMU_GUEST_AGENT" -eq 1 ]; then
        "$@" --qemu-guest-agent
    else
        "$@"
    fi
}

normalize_ref() {
    case "$1" in
        main) CHANNEL=dev ;;
        stable|dev|v*|branch-*|commit-*) CHANNEL=$1 ;;
        *) CHANNEL="branch-$1" ;;
    esac
}

validate_channel() {
    case "$CHANNEL" in
        stable|dev) ;;
        v[0-9]*)
            case "$CHANNEL" in
                *[!A-Za-z0-9.+-]*|v) fail "invalid --channel value: $CHANNEL" ;;
            esac
            ;;
        branch-*)
            branch=${CHANNEL#branch-}
            [ -n "$branch" ] || fail "invalid --channel value: $CHANNEL"
            case "$branch" in
                *[!A-Za-z0-9._/-]*|.*|*/|*..*|*//*|*@\{*)
                    fail "invalid branch channel: $CHANNEL"
                    ;;
            esac
            ;;
        commit-*)
            commit=${CHANNEL#commit-}
            [ -n "$commit" ] || fail "invalid --channel value: $CHANNEL"
            case "$commit" in
                *[!A-Fa-f0-9]*) fail "invalid commit channel: $CHANNEL" ;;
            esac
            ;;
        *) fail "invalid --channel value: $CHANNEL" ;;
    esac
}

validate_host_os() {
    [ -r /etc/os-release ] || fail "cannot detect host distribution: /etc/os-release is missing"
    HOST_OS_ID=$(sed -n 's/^ID=//p' /etc/os-release | sed 's/^"//; s/"$//' | head -n 1)
    case "$HOST_OS_ID" in
        debian|ubuntu|linuxmint) ;;
        cachyos)
            HOST_OS_SUPPORTED=0
            ;;
        *)
            HOST_OS_SUPPORTED=0
            if ! confirm_unsupported_host; then
                fail "unsupported host confirmation declined"
            fi
            ;;
    esac
}

resolve_channel_ref() {
    case "$CHANNEL" in
        stable)
            TARGET_REF=$(git -C "$STAGED_DIR" tag --list 'v[0-9]*' | awk '/^v[0-9]+\.[0-9]+\.[0-9]+$/ { print }' | sort -V | tail -n 1)
            [ -n "$TARGET_REF" ] || fail "no versioned release tags are available for stable"
            TARGET_REF="refs/tags/$TARGET_REF"
            ;;
        dev) TARGET_REF=refs/remotes/origin/main ;;
        v*) TARGET_REF="refs/tags/$CHANNEL" ;;
        branch-*) TARGET_REF="refs/remotes/origin/${CHANNEL#branch-}" ;;
        commit-*) TARGET_REF=${CHANNEL#commit-} ;;
    esac

    if ! git -C "$STAGED_DIR" rev-parse --verify --quiet "$TARGET_REF^{commit}" >/dev/null; then
        fail "channel does not exist in the repository: $CHANNEL"
    fi
}

write_channel_state() {
    state_dir="$INSTALL_DIR/.basaltwater"
    mkdir -p "$state_dir"
    commit=$(git -C "$INSTALL_DIR" rev-parse --verify HEAD)
    state_path="$state_dir/channel.json"
    temporary_state="$state_path.new.$$"
    printf '{\n  "channel": "%s",\n  "commit": "%s"\n}\n' \
        "$CHANNEL" "$commit" > "$temporary_state"
    chmod 600 "$temporary_state"
    mv "$temporary_state" "$state_path"
}

INSTALL_REF="${BASALTWATER_REF-}"
if [ -n "$INSTALL_REF" ] && [ "$CHANNEL_SET" -eq 0 ]; then
    normalize_ref "$INSTALL_REF"
    CHANNEL_SET=1
fi

while [ "$#" -gt 0 ]; do
    case "$1" in
        --channel)
            [ "$#" -ge 2 ] || fail "--channel requires a value"
            CHANNEL=$2
            CHANNEL_SET=1
            shift 2
            ;;
        --ref)
            [ "$#" -ge 2 ] || fail "--ref requires a value"
            normalize_ref "$2"
            CHANNEL_SET=1
            shift 2
            ;;
        --install-dir)
            [ "$#" -ge 2 ] || fail "--install-dir requires a value"
            INSTALL_DIR=$2
            shift 2
            ;;
        --user)
            [ "$#" -ge 2 ] || fail "--user requires a value"
            TARGET_USER=$2
            shift 2
            ;;
        --shell)
            [ "$#" -ge 2 ] || fail "--shell requires a value"
            SHELL_NAME=$2
            shift 2
            ;;
        --qemu-guest-agent)
            INSTALL_QEMU_GUEST_AGENT=1
            shift
            ;;
        --setup)
            RUN_SETUP=1
            shift
            break
            ;;
        --local-setup)
            RUN_SETUP=1
            LOCAL_SETUP_REQUESTED=1
            shift
            break
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

validate_channel
[ -n "$REPOSITORY_URL" ] || fail "repository URL must not be empty"
validate_host_os
printf '%s\n' "Basaltwater installer: host validated; checking prerequisites and package sources..."

if [ "$RUN_SETUP" -eq 1 ] && [ "$LOCAL_SETUP_REQUESTED" -eq 0 ] && [ "$#" -lt 2 ]; then
    fail "--setup requires at least SYSTEM_TYPE and HOST"
fi
if [ "$LOCAL_SETUP_REQUESTED" -eq 1 ] && [ "$#" -lt 1 ]; then
    fail "--local-setup requires SYSTEM_TYPE"
fi

CURRENT_USER=$(id -un)
if [ -z "$TARGET_USER" ]; then
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        TARGET_USER=$SUDO_USER
    else
        TARGET_USER=$CURRENT_USER
    fi
fi

if ! id "$TARGET_USER" >/dev/null 2>&1; then
    fail "user not found: $TARGET_USER"
fi
if [ "$(id -u)" -ne 0 ] && [ "$TARGET_USER" != "$CURRENT_USER" ]; then
    fail "installing for another user requires root"
fi
if [ "$INSTALL_QEMU_GUEST_AGENT" -eq 1 ] && [ "$(id -u)" -ne 0 ]; then
    fail "--qemu-guest-agent requires root; rerun the installer with sudo"
fi

TARGET_HOME=$(getent passwd "$TARGET_USER" | awk -F: 'NR == 1 { print $6 }')
[ -n "$TARGET_HOME" ] || fail "could not determine home directory for $TARGET_USER"

if [ -z "$INSTALL_DIR" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        INSTALL_DIR=/opt/basaltwater
    else
        INSTALL_DIR="${XDG_DATA_HOME:-$TARGET_HOME/.local/share}/basaltwater"
    fi
fi
case "$INSTALL_DIR" in
    /*) ;;
    *) fail "--install-dir must be an absolute path" ;;
esac
[ "$INSTALL_DIR" != "/" ] || fail "refusing to use / as the install directory"

if [ -z "$SHELL_NAME" ]; then
    USER_SHELL=$(getent passwd "$TARGET_USER" | awk -F: 'NR == 1 { print $7 }')
    SHELL_NAME=$(basename "${USER_SHELL:-bash}")
fi
case "$SHELL_NAME" in
    bash|zsh|fish|tcsh) ;;
    *) SHELL_NAME=bash ;;
esac

LOCAL_SETUP=0
if [ "$LOCAL_SETUP_REQUESTED" -eq 1 ]; then
    SETUP_SYSTEM_TYPE=$1
    shift
    set -- "$SETUP_SYSTEM_TYPE" localhost "$TARGET_USER" "$@"
    LOCAL_SETUP=1
    if [ "$(id -u)" -ne 0 ]; then
        command -v sudo >/dev/null 2>&1 || fail "local setup requires sudo; install sudo or rerun the installer with sudo"
    fi
elif [ "$RUN_SETUP" -eq 1 ]; then
    case "$2" in
        localhost|127.0.0.1|::1) LOCAL_SETUP=1 ;;
    esac

    if [ "$LOCAL_SETUP" -eq 1 ]; then
        SETUP_SYSTEM_TYPE=$1
        SETUP_HOST=$2
        shift 2
        if [ "$#" -eq 0 ] || [ "${1#-}" != "$1" ]; then
            set -- "$SETUP_SYSTEM_TYPE" "$SETUP_HOST" "$TARGET_USER" "$@"
        else
            set -- "$SETUP_SYSTEM_TYPE" "$SETUP_HOST" "$@"
        fi

        if [ "$(id -u)" -ne 0 ]; then
            command -v sudo >/dev/null 2>&1 || fail "local setup requires sudo; install sudo or rerun the installer with sudo"
        fi
    fi
fi

if [ "$HOST_OS_SUPPORTED" -eq 0 ] && [ "$INSTALL_QEMU_GUEST_AGENT" -eq 1 ]; then
    fail "--qemu-guest-agent requires the Debian package setup path; install the launcher without this option on an unsupported host"
fi
if [ "$HOST_OS_ID" = cachyos ]; then
    [ "$(id -u)" -ne 0 ] || fail "run the CachyOS installer as your desktop user without sudo"
    [ -z "${SSH_CONNECTION:-}${SSH_TTY:-}" ] || fail "run CachyOS setup locally in a desktop terminal"
    if [ "$LOCAL_SETUP" -eq 1 ] && [ "$SETUP_SYSTEM_TYPE" != agent_cachyos ]; then
        fail "CachyOS local setup supports only agent_cachyos"
    fi
fi
if [ "$RUN_SETUP" -eq 1 ] && [ "$1" = agent_cachyos ]; then
    [ "$HOST_OS_ID" = cachyos ] && [ "$LOCAL_SETUP" -eq 1 ] || fail "agent_cachyos requires local setup on CachyOS"
fi
if [ "$HOST_OS_SUPPORTED" -eq 0 ] && [ "$HOST_OS_ID" != cachyos ] && [ "$LOCAL_SETUP" -eq 1 ]; then
    fail "local setup is not supported on an unsupported host; install the launcher and use remote setup instead"
fi

missing_prerequisite=0
missing_prerequisites=""
for command_name in python3 git ssh rsync; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        missing_prerequisite=1
        if [ -n "$missing_prerequisites" ]; then
            missing_prerequisites="$missing_prerequisites, "
        fi
        missing_prerequisites="$missing_prerequisites$command_name"
    fi
done
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    missing_prerequisite=1
    if [ -n "$missing_prerequisites" ]; then
        missing_prerequisites="$missing_prerequisites, "
    fi
    missing_prerequisites="${missing_prerequisites}curl or wget"
fi

ensure_debian_bootstrap_sources() {
    if [ "$HOST_OS_ID" != "debian" ]; then
        return
    fi

    distro_codename=$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release | sed 's/^"//; s/"$//' | head -n 1)
    case "$distro_codename" in
        [a-z]*)
            case "$distro_codename" in
                *[!a-z0-9-]*) fail "invalid Debian release codename: $distro_codename" ;;
            esac
            ;;
        *) fail "could not determine Debian release codename" ;;
    esac

    # A minimal install may have only a CD/DVD source and may not yet have
    # Python available, so repair the bootstrap source configuration in POSIX
    # shell before attempting to install the installer's prerequisites.
    run_privileged sh -s -- "$distro_codename" <<'EOF'
set -eu

codename=$1
apt_dir=/etc/apt
source_dir="$apt_dir/sources.list.d"
keyring=/usr/share/keyrings/debian-archive-keyring.pgp
if [ ! -r "$keyring" ]; then
    keyring=/usr/share/keyrings/debian-archive-keyring.gpg
fi
managed_path="$source_dir/basaltwater-debian.sources"

[ -r "$keyring" ] || {
    printf '%s\n' "Basaltwater installer: Debian archive keyring is missing at $keyring" >&2
    exit 1
}

mkdir -p "$source_dir"

for source_path in "$apt_dir/sources.list" "$source_dir"/*.list; do
    [ -f "$source_path" ] || continue
    if grep -Eq '^[[:space:]]*deb[[:space:]]+(\[[^]]*\][[:space:]]+)?cdrom:' "$source_path"; then
        backup_path="$source_path.basaltwater.bak"
        [ -e "$backup_path" ] || cp -p "$source_path" "$backup_path"
        sed -i -E 's/^([[:space:]]*deb[[:space:]]+(\[[^]]*\][[:space:]]+)?cdrom:)/# Disabled by basaltwater: \1/' "$source_path"
    fi
done

for source_path in "$source_dir"/*.sources; do
    [ -f "$source_path" ] || continue
    if awk '
        /^[[:space:]]*#/ { next }
        /(^|[[:space:]])cdrom:/ { found=1; exit }
        END { exit found ? 0 : 1 }
    ' "$source_path"; then
        backup_path="$source_path.basaltwater.bak"
        [ -e "$backup_path" ] || cp -p "$source_path" "$backup_path"
        sed -i 's/^/# Disabled by basaltwater: /' "$source_path"
    fi
done

has_current_base=0
has_current_security=0
for source_path in "$apt_dir/sources.list" "$source_dir"/*.list; do
    [ -f "$source_path" ] || continue
    [ "$source_path" = "$managed_path" ] && continue
    if grep -Eq "^[[:space:]]*deb[[:space:]]+(\\[[^]]*\\][[:space:]]+)?https?://deb[.]debian[.]org/debian/?([[:space:]]|$)" "$source_path" \
        && grep -Eq "^[[:space:]]*deb.*[[:space:]]${codename}([[:space:]]|$)" "$source_path"; then
        has_current_base=1
    fi
    if grep -Eq "^[[:space:]]*deb[[:space:]]+(\\[[^]]*\\][[:space:]]+)?https?://security[.]debian[.]org/debian-security/?([[:space:]]|$)" "$source_path" \
        && grep -Eq "^[[:space:]]*deb.*[[:space:]]${codename}-security([[:space:]]|$)" "$source_path"; then
        has_current_security=1
    fi
done

for source_path in "$source_dir"/*.sources; do
    [ -f "$source_path" ] || continue
    [ "$source_path" = "$managed_path" ] && continue
    if grep -Eq '^[[:space:]]*URIs:[[:space:]]*https?://deb[.]debian[.]org/debian/?([[:space:]]|$)' "$source_path" \
        && grep -Eq "^[[:space:]]*Suites:.*([[:space:]]|^)${codename}([[:space:]]|$)" "$source_path"; then
        has_current_base=1
    fi
    if grep -Eq '^[[:space:]]*URIs:[[:space:]]*https?://security[.]debian[.]org/debian-security/?([[:space:]]|$)' "$source_path" \
        && grep -Eq "^[[:space:]]*Suites:.*([[:space:]]|^)${codename}-security([[:space:]]|$)" "$source_path"; then
        has_current_security=1
    fi
done

if [ "$has_current_base" -eq 1 ] && [ "$has_current_security" -eq 1 ]; then
    if [ -e "$managed_path" ]; then
        grep -q '^# Managed by basaltwater' "$managed_path" || {
            printf '%s\n' "Basaltwater installer: refusing to remove unmanaged APT source $managed_path" >&2
            exit 1
        }
        backup_path="$managed_path.basaltwater.bak"
        [ -e "$backup_path" ] || cp -p "$managed_path" "$backup_path"
        rm -f "$managed_path"
        printf '%s\n' "Removed redundant basaltwater APT source; existing Debian sources already cover $codename."
    fi
    exit 0
fi

temporary_path="$managed_path.new.$$"
printf '%s\n' '# Managed by basaltwater. Do not edit; rerun basaltw after a Debian release change.' > "$temporary_path"
if [ "$has_current_base" -eq 0 ]; then
    cat >> "$temporary_path" <<SOURCE

Types: deb
URIs: https://deb.debian.org/debian
Suites: $codename $codename-updates
Components: main non-free-firmware
Signed-By: $keyring
SOURCE
fi
if [ "$has_current_security" -eq 0 ]; then
    cat >> "$temporary_path" <<SOURCE

Types: deb
URIs: https://security.debian.org/debian-security
Suites: $codename-security
Components: main non-free-firmware
Signed-By: $keyring
SOURCE
fi
chmod 0644 "$temporary_path"

if [ -e "$managed_path" ]; then
    grep -q '^# Managed by basaltwater' "$managed_path" || {
        rm -f "$temporary_path"
        printf '%s\n' "Basaltwater installer: refusing to overwrite unmanaged APT source $managed_path" >&2
        exit 1
    }
    if cmp -s "$temporary_path" "$managed_path"; then
        rm -f "$temporary_path"
    else
        backup_path="$managed_path.basaltwater.bak"
        [ -e "$backup_path" ] || cp -p "$managed_path" "$backup_path"
        mv "$temporary_path" "$managed_path"
    fi
else
    mv "$temporary_path" "$managed_path"
fi
EOF
}

if [ "$HOST_OS_ID" = cachyos ] && [ "$missing_prerequisite" -eq 1 ]; then
    command -v pacman >/dev/null 2>&1 || fail "CachyOS prerequisites require pacman"
    printf '%s\n' "Installing missing prerequisites; repository refresh and OS updates remain user-managed."
    # Query each package so reruns retain installed versions even if the sync
    # database is newer. Never use -Sy or -Syu in the installer.
    for package in ca-certificates curl git openssh python rsync tar; do
        if pacman -Q -- "$package" >/dev/null 2>&1; then
            continue
        fi
        run_privileged pacman -S --needed --noconfirm -- "$package" || fail "resolve the pacman error, update CachyOS normally if needed, and rerun"
    done
fi
if [ "$HOST_OS_SUPPORTED" -eq 0 ] && [ "$HOST_OS_ID" != cachyos ] && [ "$missing_prerequisite" -eq 1 ]; then
    fail "unsupported host is missing required controller commands: $missing_prerequisites; install them manually and rerun the installer"
fi

if [ "$HOST_OS_SUPPORTED" -eq 1 ] && [ "$missing_prerequisite" -eq 1 ]; then
    command -v apt-get >/dev/null 2>&1 || fail "automatic prerequisite installation requires apt-get"
    ensure_debian_bootstrap_sources
    printf '%s\n' "Refreshing package lists (APT may wait for another package operation)..."
    run_with_progress run_privileged env DEBIAN_FRONTEND=noninteractive apt-get \
        -o DPkg::Lock::Timeout=120 \
        -o Dpkg::Use-Pty=0 \
        update -q
    printf '%s\n' "Installing bootstrap prerequisites..."
    run_with_progress run_privileged env DEBIAN_FRONTEND=noninteractive apt-get \
        -o DPkg::Lock::Timeout=120 \
        -o Dpkg::Use-Pty=0 \
        install -y -q \
        ca-certificates git openssh-client python3 rsync
fi

python3 - "$INSTALL_DIR" "$TARGET_HOME" <<'EOF'
from __future__ import annotations
import os
from pathlib import Path
import sys

target = Path(sys.argv[1])
home = Path(sys.argv[2])
def refuse(reason: str) -> None:
    sys.exit(f"Basaltwater installer: refusing install directory {target}: {reason}")

if str(target) != os.path.normpath(sys.argv[1]) or '..' in target.parts:
    refuse('use a normalized absolute path')
if target == home or target in home.parents:
    refuse('home directory or its ancestor')
if len(target.parts) < 3 or str(target) in ('/var/lib', '/usr/local', '/usr/share', '/var/cache'):
    refuse('use a dedicated application directory')
for part in (target, *target.parents):
    if part.is_symlink():
        refuse('symlink path component')
if os.path.ismount(target):
    refuse('mount point')
if target.exists():
    marker = target / '.basaltwater' / 'managed-install'
    managed = marker.is_file() and not marker.is_symlink() and marker.read_text() == 'basaltwater-v1\n'
    data_only = (
        target == home / '.local/share/basaltwater'
        and target.is_dir()
        and (target / 'cachyos-t3').is_dir()
        and not (target / 'basaltwater.py').exists()
        and not (target / '.git').exists()
    )
    migrated = (
        target.name == 'basaltwater'
        and (target / 'basaltwater.py').is_file()
        and any(target.parent.glob('.basaltwater-migration-*'))
    )
    if not managed and not data_only and not migrated:
        refuse('unmanaged directory; use basaltw migrate for recent infra-tools installations')
EOF

if [ "$CHANNEL_SET" -eq 0 ] && [ -f "$INSTALL_DIR/.basaltwater/channel.json" ]; then
    existing_channel=$(python3 -c \
        'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("channel", ""))' \
        "$INSTALL_DIR/.basaltwater/channel.json" 2>/dev/null || true)
    if [ -n "$existing_channel" ]; then
        CHANNEL=$existing_channel
        validate_channel
    fi
fi

if [ -e "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR/.git" ]; then
    if [ -d "$INSTALL_DIR/cachyos-t3" ] && [ ! -L "$INSTALL_DIR/cachyos-t3" ]; then
        existing_changes=$(git -C "$INSTALL_DIR" status --porcelain -- . ':(exclude)cachyos-t3')
    else
        existing_changes=$(git -C "$INSTALL_DIR" status --porcelain)
    fi
    if [ -n "$existing_changes" ]; then
        fail "existing install has local changes; commit or stash them before reinstalling"
    fi
fi

BACKUP_DIR=""
STAGED_DIR="${INSTALL_DIR}.new.$$"
ACTIVATION_PENDING=0
rollback_install() {
    # If the first rename never happened, the original tree is still active.
    if [ -n "$BACKUP_DIR" ] && [ ! -e "$BACKUP_DIR" ]; then
        return
    fi
    FAILED_DIR="${INSTALL_DIR}.failed.$$"
    if [ -e "$INSTALL_DIR" ]; then
        if [ -e "$FAILED_DIR" ] || ! mv "$INSTALL_DIR" "$FAILED_DIR"; then
            printf 'Recovery required: active=%s backup=%s staged=%s\n' "$INSTALL_DIR" "$BACKUP_DIR" "$STAGED_DIR" >&2
            return 1
        fi
    fi
    if [ -n "$BACKUP_DIR" ]; then
        if ! mv "$BACKUP_DIR" "$INSTALL_DIR"; then
            printf 'Recovery required: restore %s to %s; failed source=%s\n' "$BACKUP_DIR" "$INSTALL_DIR" "$FAILED_DIR" >&2
            return 1
        fi
        printf 'Installation failed; previous install restored. Failed source: %s; staged source: %s\n' "$FAILED_DIR" "$STAGED_DIR" >&2
    else
        printf 'Installation failed; failed source: %s; staged source: %s\n' "$FAILED_DIR" "$STAGED_DIR" >&2
    fi
}
cleanup() {
    install_status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if [ "$ACTIVATION_PENDING" -eq 1 ]; then
        rollback_install || install_status=1
    fi
    exit "$install_status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

INSTALL_PARENT=$(dirname "$INSTALL_DIR")
mkdir -p "$INSTALL_PARENT"
[ ! -e "$STAGED_DIR" ] && [ ! -L "$STAGED_DIR" ] || fail "staging path already exists: $STAGED_DIR"

printf 'Cloning Basaltwater repository (%s)...\n' "$CHANNEL"
if ! git clone "$REPOSITORY_URL" "$STAGED_DIR"; then
    fail "could not clone repository: $REPOSITORY_URL"
fi
if ! git -C "$STAGED_DIR" fetch --prune --tags origin; then
    fail "could not fetch repository refs"
fi
resolve_channel_ref
if ! git -C "$STAGED_DIR" checkout --detach "$TARGET_REF" >/dev/null; then
    fail "could not check out channel: $CHANNEL"
fi

[ -f "$STAGED_DIR/basaltwater.py" ] || fail "selected source predates Basaltwater; historical releases are unsupported"

if [ "$HOST_OS_ID" = cachyos ]; then
    printf '\nChecking for recent infra-tools data to migrate...\n'
    if ! env HOME="$TARGET_HOME" USER="$TARGET_USER" \
        python3 "$STAGED_DIR/basaltwater.py" migrate --apply; then
        fail "CachyOS Basaltwater migration failed; preserve any migration journal and resolve its reported conflict before retrying"
    fi
fi

python3 - "$INSTALL_DIR" "$TARGET_HOME" <<'EOF'
from __future__ import annotations
import os
from pathlib import Path
import sys

target = Path(sys.argv[1])
home = Path(sys.argv[2])
def refuse(reason: str) -> None:
    sys.exit(f"Basaltwater installer: refusing install directory {target}: {reason}")

if str(target) != os.path.normpath(sys.argv[1]) or '..' in target.parts:
    refuse('use a normalized absolute path')
if target == home or target in home.parents:
    refuse('home directory or its ancestor')
if len(target.parts) < 3 or str(target) in ('/var/lib', '/usr/local', '/usr/share', '/var/cache'):
    refuse('use a dedicated application directory')
for part in (target, *target.parents):
    if part.is_symlink():
        refuse('symlink path component')
if os.path.ismount(target):
    refuse('mount point')
if target.exists():
    marker = target / '.basaltwater' / 'managed-install'
    managed = marker.is_file() and not marker.is_symlink() and marker.read_text() == 'basaltwater-v1\n'
    data_only = (
        target == home / '.local/share/basaltwater'
        and target.is_dir()
        and (target / 'cachyos-t3').is_dir()
        and not (target / 'basaltwater.py').exists()
        and not (target / '.git').exists()
    )
    migrated = (
        target.name == 'basaltwater'
        and (target / 'basaltwater.py').is_file()
        and any(target.parent.glob('.basaltwater-migration-*'))
    )
    if not managed and not data_only and not migrated:
        refuse('unmanaged directory; use basaltw migrate for recent infra-tools installations')
EOF

if [ -e "$INSTALL_DIR" ]; then
    BACKUP_DIR="${INSTALL_DIR}.backup.$(date +%s).$$"
    [ ! -e "$BACKUP_DIR" ] && [ ! -L "$BACKUP_DIR" ] || fail "backup path already exists: $BACKUP_DIR"
fi
# Set the rollback guard before either rename, including signal boundaries.
ACTIVATION_PENDING=1
if [ -n "$BACKUP_DIR" ]; then
    mv "$INSTALL_DIR" "$BACKUP_DIR"
fi
if ! mv "$STAGED_DIR" "$INSTALL_DIR"; then
    fail "could not activate downloaded source"
fi
if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR/state" ]; then
    if ! cp -a "$BACKUP_DIR/state" "$INSTALL_DIR/state"; then
        fail "could not preserve existing basaltwater state"
    fi
fi
if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR/.basaltwater" ]; then
    if ! cp -a "$BACKUP_DIR/.basaltwater" "$INSTALL_DIR/.basaltwater"; then
        fail "could not preserve existing channel state"
    fi
fi
if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR/cachyos-t3" ]; then
    if [ -e "$INSTALL_DIR/cachyos-t3" ]; then
        fail "could not preserve existing CachyOS T3 data"
    fi
    if ! cp -a "$BACKUP_DIR/cachyos-t3" "$INSTALL_DIR/cachyos-t3"; then
        fail "could not preserve existing CachyOS T3 data"
    fi
fi
write_channel_state
printf 'basaltwater-v1\n' > "$INSTALL_DIR/.basaltwater/managed-install"

if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
    TARGET_UID=$(getent passwd "$TARGET_USER" | awk -F: 'NR == 1 { print $3 }')
    TARGET_GID=$(getent passwd "$TARGET_USER" | awk -F: 'NR == 1 { print $4 }')
    if [ -z "$TARGET_UID" ] || [ -z "$TARGET_GID" ]; then
        fail "could not determine target user ownership"
    fi
    if ! chown -R "$TARGET_UID:$TARGET_GID" "$INSTALL_DIR"; then
        fail "could not assign the managed repository to $TARGET_USER"
    fi
fi

ENTRY_SCRIPT="$INSTALL_DIR/basaltwater.py"
printf 'Installing Basaltwater for %s...\n' "$TARGET_USER"
if [ "$(id -u)" -eq 0 ]; then
    if ! run_bootstrap env HOME="$TARGET_HOME" python3 "$ENTRY_SCRIPT" bootstrap \
        --shell "$SHELL_NAME" \
        --user "$TARGET_USER"; then
        exit 1
    fi
else
    if ! run_bootstrap env HOME="$TARGET_HOME" USER="$TARGET_USER" python3 "$ENTRY_SCRIPT" bootstrap \
        --shell "$SHELL_NAME" \
        --user "$TARGET_USER" \
        --skip-system-packages; then
        exit 1
    fi
fi

USER_LAUNCHER="$TARGET_HOME/.local/bin/basaltw"
if [ ! -x "$USER_LAUNCHER" ]; then
    fail "bootstrap completed without creating $USER_LAUNCHER"
fi
if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
    chmod 700 "$BACKUP_DIR"
fi
ACTIVATION_PENDING=0

printf '\nBasaltwater installed successfully.\n'
printf '  Source: %s\n' "$INSTALL_DIR"
printf '  Channel: %s\n' "$CHANNEL"
printf '  Command: %s\n' "$USER_LAUNCHER"
if [ -n "$BACKUP_DIR" ]; then
    printf '  Previous source backup: %s\n' "$BACKUP_DIR"
fi
case ":${PATH:-}:" in
    *":$TARGET_HOME/.local/bin:"*) ;;
    *) printf '  Note: add %s/.local/bin to PATH or start a new login shell.\n' "$TARGET_HOME" ;;
esac

run_for_target() {
    if [ "$TARGET_USER" = "$CURRENT_USER" ]; then
        HOME=$TARGET_HOME USER=$TARGET_USER "$@"
    else
        runuser -u "$TARGET_USER" -- env HOME="$TARGET_HOME" USER="$TARGET_USER" "$@"
    fi
}

run_local_setup() {
    if [ "$HOST_OS_ID" = cachyos ]; then
        run_for_target python3 "$ENTRY_SCRIPT" setup "$@"
        return
    fi
    if [ -t 2 ] && [ -r /dev/tty ]; then
        run_privileged env \
            HOME="$TARGET_HOME" \
            USER="$TARGET_USER" \
            SUDO_USER="$TARGET_USER" \
            python3 "$ENTRY_SCRIPT" setup "$@" < /dev/tty
    else
        run_privileged env \
            HOME="$TARGET_HOME" \
            USER="$TARGET_USER" \
            SUDO_USER="$TARGET_USER" \
            python3 "$ENTRY_SCRIPT" setup "$@"
    fi
}

if [ "$RUN_SETUP" -eq 1 ]; then
    printf '\nRunning requested system setup as %s...\n' "$TARGET_USER"
    if [ "$LOCAL_SETUP" -eq 1 ]; then
        run_local_setup "$@"
    elif [ -t 2 ] && [ -r /dev/tty ]; then
        run_for_target python3 "$ENTRY_SCRIPT" setup "$@" < /dev/tty
    else
        run_for_target python3 "$ENTRY_SCRIPT" setup "$@"
    fi
fi
