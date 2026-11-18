#!/bin/sh

set -eu

REPOSITORY="bluehexagons/basaltwater"
OFFICIAL_URL="https://github.com/$REPOSITORY.git"
# Explicit values, including empty settings, are validated below.
REPOSITORY_URL="${BASALTWATER_REPOSITORY_URL-$OFFICIAL_URL}"
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
PLAN_ONLY=0
SOURCE_COMMIT=""

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
  --source-commit SHA    Require this full Git commit before activating source
                        (new system installs and legacy migrations need a pin)
  --install-dir PATH   Source destination (default: /opt/basaltwater as root,
                       otherwise ~/.local/share/basaltwater)
  --user USER          User receiving local tools and completions
  --shell SHELL        bash, zsh, fish, or tcsh (default: target user's shell)
  --plan               Print the installer plan without downloads or changes
  --qemu-guest-agent   Install, start, and enable Proxmox's qemu-guest-agent
                       during self-setup (must appear before --setup/--local-setup)
  --setup ...          Run `basaltw setup ...` after installation
  --local-setup TYPE   Run `basaltw setup TYPE localhost USER` after installation
                       (the target user comes from --user or the invoking user)
  -h, --help           Show this help

Examples:
  sh install.sh --plan --local-setup agent_cachyos --t3code-desktop
  sh install.sh --local-setup agent_cachyos --t3code-desktop --node --python
  sudo sh install.sh --user "$USER" --source-commit "$reviewed_commit" \
    --local-setup control_plane --agent-tool gh

Download and run a user install from fish, Bash, or Zsh (copy the whole block):
  sh -c '
    installer=$(mktemp) || exit
    cleanup() { rm -f -- "$installer"; }
    trap cleanup EXIT
    curl -fsSL --max-time 120 https://raw.githubusercontent.com/bluehexagons/basaltwater/main/install.sh -o "$installer" &&
    sh "$installer" "$@"
  ' sh
EOF
}

fail() {
    printf 'Basaltwater installer: %s\n' "$*" >&2
    exit 1
}

require_supported_python() {
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        fail "Python 3.11 or newer is required; use a distribution with a supported system Python. The installer does not upgrade the OS release."
    fi
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
            if [ "$PLAN_ONLY" -eq 0 ] && ! confirm_unsupported_host; then
                fail "unsupported host confirmation declined"
            fi
            ;;
    esac
}

resolve_channel_ref() {
    case "$CHANNEL" in
        stable)
            TARGET_REF=$(source_git -C "$STAGED_DIR" tag --list 'v[0-9]*' | awk '/^v[0-9]+\.[0-9]+\.[0-9]+$/ { print }' | sort -V | tail -n 1)
            [ -n "$TARGET_REF" ] || fail "no versioned release tags are available for stable"
            TARGET_REF="refs/tags/$TARGET_REF"
            ;;
        dev) TARGET_REF=refs/remotes/origin/main ;;
        v*) TARGET_REF="refs/tags/$CHANNEL" ;;
        branch-*) TARGET_REF="refs/remotes/origin/${CHANNEL#branch-}" ;;
        commit-*) TARGET_REF=${CHANNEL#commit-} ;;
    esac

    if [ "${CHANNEL#commit-}" != "$CHANNEL" ]; then
        if [ "$(printf %s "${CHANNEL#commit-}" | wc -c)" -eq 40 ]; then
            source_git -C "$STAGED_DIR" cat-file -e "$TARGET_REF^{commit}" 2>/dev/null ||
                source_git -C "$STAGED_DIR" fetch --no-tags origin "$TARGET_REF"
        fi
    fi
    if ! RESOLVED_COMMIT=$(source_git -C "$STAGED_DIR" rev-parse --verify --quiet "$TARGET_REF^{commit}"); then
        fail "channel does not exist in the repository: $CHANNEL"
    fi
    if [ -n "$SOURCE_COMMIT" ] && [ "$RESOLVED_COMMIT" != "$SOURCE_COMMIT" ]; then
        fail "selected source commit $RESOLVED_COMMIT does not match expected $SOURCE_COMMIT"
    fi
}

write_channel_state() {
    state_dir="$STAGED_DIR/.basaltwater"
    mkdir -p "$state_dir"
    state_path="$state_dir/channel.json"
    temporary_state=$(mktemp "$state_path.new.XXXXXX")
    printf '{\n  "channel": "%s",\n  "commit": "%s"\n}\n' \
        "$CHANNEL" "$RESOLVED_COMMIT" > "$temporary_state"
    mv "$temporary_state" "$state_path"
}

source_git() {
    if [ "$SYSTEM_INSTALL" -eq 0 ]; then
        git "$@"
        return
    fi
    # Fetch system source without caller-supplied Git config, hooks, helpers or
    # transport redirects. The administrator trusts the official HTTPS origin.
    env -i PATH="$PATH" LC_ALL=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        GIT_TERMINAL_PROMPT=0 git --no-replace-objects -c core.hooksPath=/dev/null \
        -c protocol.allow=never -c protocol.https.allow=always \
        -c http.followRedirects=false -c http.sslVerify=true "$@"
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
        --source-commit)
            [ "$#" -ge 2 ] || fail "--source-commit requires a full commit SHA"
            case "$2" in
                ''|*[!A-Fa-f0-9]*) fail "--source-commit requires a full commit SHA" ;;
            esac
            [ "${#2}" -eq 40 ] || fail "--source-commit requires a full commit SHA"
            SOURCE_COMMIT=$(printf %s "$2" | tr A-F a-f)
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
        --plan)
            PLAN_ONLY=1
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
SYSTEM_INSTALL=0
if [ "$(id -u)" -eq 0 ]; then
    SYSTEM_INSTALL=1
    [ "$REPOSITORY_URL" = "$OFFICIAL_URL" ] || fail "system installations require $OFFICIAL_URL"
fi
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
    fi
fi

if [ "$HOST_OS_SUPPORTED" -eq 0 ] && [ "$INSTALL_QEMU_GUEST_AGENT" -eq 1 ]; then
    fail "--qemu-guest-agent requires the Debian package setup path; install the launcher without this option on an unsupported host"
fi
if [ "$HOST_OS_ID" = cachyos ]; then
    [ "$(id -u)" -ne 0 ] || fail "run the CachyOS installer as your desktop user without sudo"
    if [ "$LOCAL_SETUP" -eq 1 ] && [ "$SETUP_SYSTEM_TYPE" != agent_cachyos ]; then
        fail "CachyOS local setup supports only agent_cachyos"
    fi
fi
if [ "$RUN_SETUP" -eq 1 ] && [ "$1" = agent_cachyos ]; then
    if [ "$LOCAL_SETUP" -eq 1 ]; then
        [ "$HOST_OS_ID" = cachyos ] || fail "agent_cachyos local setup requires a CachyOS controller"
    fi
fi
if [ "$HOST_OS_SUPPORTED" -eq 0 ] && [ "$HOST_OS_ID" != cachyos ] && [ "$LOCAL_SETUP" -eq 1 ]; then
    fail "local setup is not supported on an unsupported host; install the launcher and use remote setup instead"
fi
if [ "$LOCAL_SETUP" -eq 1 ] && [ "$SYSTEM_INSTALL" -eq 0 ] &&
    [ "$HOST_OS_ID" != cachyos ] && [ "$PLAN_ONLY" -eq 0 ]; then
    for setup_arg in "$@"; do
        [ "$setup_arg" != --dry-run ] || break
    done
    [ "$setup_arg" = --dry-run ] ||
        fail "rerun the reviewed installer with sudo for local system setup"
fi

missing_prerequisite=0
missing_prerequisites=""
for command_name in python3 git ssh rsync curl; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        missing_prerequisite=1
        if [ -n "$missing_prerequisites" ]; then
            missing_prerequisites="$missing_prerequisites, "
        fi
        missing_prerequisites="$missing_prerequisites$command_name"
    fi
done
# Refuse an old interpreter before package, download or source changes, even
# during a plan. A missing interpreter is checked after prerequisite installation.
if command -v python3 >/dev/null 2>&1; then
    require_supported_python
fi
if [ "$PLAN_ONLY" -eq 1 ]; then
    printf 'Installer plan for %s (%s):\n' "$TARGET_USER" "$HOST_OS_ID"
    printf '  Source destination: %s\n  Requested channel: %s\n' "$INSTALL_DIR" "$CHANNEL"
    if [ -n "$SOURCE_COMMIT" ]; then
        printf '  Required source commit: %s\n' "$SOURCE_COMMIT"
    fi
    printf '  Missing prerequisites: %s\n' "${missing_prerequisites:-none}"
    printf '  Would download source, validate it, preserve managed data, and install the launcher.\n'
    if [ "$RUN_SETUP" -eq 1 ]; then
        printf '  Would run setup with arguments:\n'
        printf '    %s\n' "$@"
    fi
    printf 'No packages, source, launcher, or setup changed. Channel resolution and target checks occur on apply.\n'
    exit 0
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

# Inspect complete entries rather than matching unrelated lines in a file.
# Python is not yet available here; keep the parser compatible with POSIX awk.
inspect_source_file() {
    awk -v action="$1" -v format="$2" -v codename="$codename" '
        function trim(value) {
            sub(/^[[:space:]]+/, "", value)
            sub(/[[:space:]]+$/, "", value)
            return value
        }
        function has_word(value, word, words, count, i) {
            count=split(trim(value), words, /[[:space:]]+/)
            for (i=1; i<=count; i++) if (words[i] == word) return 1
            return 0
        }
        function inspect_entry(uris, suites, components, words, count, i, uri) {
            if (!has_word(components, "main")) return
            count=split(trim(uris), words, /[[:space:]]+/)
            for (i=1; i<=count; i++) {
                uri=words[i]
                # URL paths are case-sensitive; normalize only the origin.
                if (match(uri, /^[^:]+:\/\/[^/]+/))
                    uri=tolower(substr(uri, 1, RLENGTH)) substr(uri, RLENGTH+1)
                if (uri ~ /^https?:\/\/deb[.]debian[.]org\/debian\/?$/ && has_word(suites, codename))
                    base=1
                if (uri ~ /^https?:\/\/(security[.]debian[.]org(\/debian-security)?|deb[.]debian[.]org\/debian-security)\/?$/ && has_word(suites, codename "-security"))
                    security=1
            }
        }
        function finish_stanza(disable, lines, count, i) {
            disable=0
            if (tolower(trim(fields["enabled"])) != "no") {
                if (action == "coverage") {
                    if (has_word(tolower(fields["types"]), "deb"))
                        inspect_entry(fields["uris"], fields["suites"], fields["components"])
                } else if ((has_word(tolower(fields["types"]), "deb") || has_word(tolower(fields["types"]), "deb-src")) && tolower(fields["uris"]) ~ /(^|[[:space:]])cdrom:/)
                    disable=1
            }
            if (disable) cdrom=1
            if (action == "disable-cdrom") {
                if (disable) {
                    count=split(stanza, lines, "\n")
                    for (i=1; i<count; i++) print "# Disabled by basaltwater: " lines[i]
                } else printf "%s", stanza
            }
            for (i in fields) delete fields[i]
            stanza=""
            field=""
        }
        format == "list" {
            line=trim($0)
            sub(/[[:space:]]*#.*/, "", line)
            if (line !~ /^deb[[:space:]]+/) next
            sub(/^deb[[:space:]]+/, "", line)
            sub(/^\[[^]]*\][[:space:]]+/, "", line)
            count=split(line, words, /[[:space:]]+/)
            components=""
            for (i=3; i<=count; i++) components=components " " words[i]
            inspect_entry(words[1], words[2], components)
            next
        }
        /^[[:space:]]*$/ {
            finish_stanza()
            if (action == "disable-cdrom") print
            next
        }
        {
            stanza=stanza $0 "\n"
            if ($0 ~ /^[[:space:]]*#/) next
            if ($0 ~ /^[[:space:]]/ && field != "") {
                fields[field]=fields[field] " " trim($0)
            } else {
                separator=index($0, ":")
                field=""
                if (separator) {
                    field=tolower(trim(substr($0, 1, separator-1)))
                    fields[field]=trim(substr($0, separator+1))
                }
            }
        }
        END {
            if (format == "sources") finish_stanza()
            if (action == "coverage") {
                if (base) print "base"
                if (security) print "security"
            }
            if (action == "cdrom" && cdrom) print "cdrom"
        }
    ' "$3"
}

for source_path in "$apt_dir/sources.list" "$source_dir"/*.list; do
    [ -f "$source_path" ] || continue
    if grep -Eq '^[[:space:]]*deb(-src)?[[:space:]]+(\[[^]]*\][[:space:]]+)?cdrom:' "$source_path"; then
        backup_path="$source_path.basaltwater.bak"
        [ -e "$backup_path" ] || cp -p "$source_path" "$backup_path"
        sed -i -E 's/^([[:space:]]*deb(-src)?[[:space:]]+(\[[^]]*\][[:space:]]+)?cdrom:)/# Disabled by basaltwater: \1/' "$source_path"
    fi
done

for source_path in "$source_dir"/*.sources; do
    [ -f "$source_path" ] || continue
    cdrom=$(inspect_source_file cdrom sources "$source_path")
    [ -n "$cdrom" ] || continue
    temporary_path=$(mktemp "$source_path.basaltwater.XXXXXX")
    # Retain the source file mode and owner when replacing a changed file.
    cp -p "$source_path" "$temporary_path"
    inspect_source_file disable-cdrom sources "$source_path" > "$temporary_path"
    if cmp -s "$source_path" "$temporary_path"; then
        rm -f "$temporary_path"
    else
        backup_path="$source_path.basaltwater.bak"
        [ -e "$backup_path" ] || cp -p "$source_path" "$backup_path"
        mv "$temporary_path" "$source_path"
    fi
done

has_current_base=0
has_current_security=0
for source_path in "$apt_dir/sources.list" "$source_dir"/*.list "$source_dir"/*.sources; do
    [ -f "$source_path" ] || continue
    [ "$source_path" = "$managed_path" ] && continue
    case "$source_path" in
        *.sources) source_format=sources ;;
        *) source_format=list ;;
    esac
    coverage=$(inspect_source_file coverage "$source_format" "$source_path")
    for suite in $coverage; do
        case "$suite" in
            base) has_current_base=1 ;;
            security) has_current_security=1 ;;
        esac
    done
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
        ca-certificates curl git openssh-client python3 rsync
fi

require_supported_python

validate_install_directory() {
    python3 -I - "${1:-$INSTALL_DIR}" "$TARGET_HOME" "$HOST_OS_ID" "$SYSTEM_INSTALL" <<'EOF'
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

target = Path(sys.argv[1])
home = Path(sys.argv[2])
system = sys.argv[4] == "1"

def refuse(reason: str) -> None:
    sys.exit(f"Basaltwater installer: refusing install directory {target}: {reason}")

def protected(info: os.stat_result) -> bool:
    return info.st_uid == os.geteuid() and not info.st_mode & 0o022

if sys.argv[1] != str(target) or '..' in target.parts:
    refuse('use a normalized absolute path')
if target == home or target in home.parents:
    refuse('home directory or its ancestor')
if len(target.parts) < 3 or str(target) in ('/var/lib', '/usr/local', '/usr/share', '/var/cache'):
    refuse('use a dedicated application directory')
for path in (target, *target.parents):
    if path.is_symlink():
        refuse(f'symlink path component: {path}')
    if system and path != target:
        info = path.lstat() if path.exists() else None
        if info and (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()} or (
            info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
        )):
            refuse(f'unprotected system install ancestor: {path}')
        # A sticky shared ancestor protects a trusted existing child only.
        if not path.exists() and path.parent.is_dir() and path.parent.stat().st_mode & 0o022:
            refuse(f'create the protected install parent first: {path}')

if os.path.ismount(target):
    refuse('mount point')
for parent in (home / '.config', home / '.cache', home / '.local/share', home / '.local/state', target.parent):
    for name in ('infra_tools', 'infra-tools'):
        if os.path.lexists(parent / name):
            refuse('retired infra-tools data; use the intermediate version in docs/migration-guide.md first')

if not target.exists():
    if system and target.parent.is_dir() and target.parent.stat().st_mode & 0o022:
        refuse('create a protected install parent first')
    print('fresh')
    sys.exit()
if not target.is_dir():
    refuse('expected a directory')

data = {'state', 'deployments', 'worktrees', 'cachyos-t3'}
def safe_checkout() -> bool:
    def fail_walk(error: OSError) -> None:
        raise error

    if not protected(target.lstat()):
        return False
    for directory, dirs, files in os.walk(target, onerror=fail_walk):
        if directory == str(target):
            dirs[:] = [name for name in dirs if name not in data]
            files = [name for name in files if name not in data]
        for name in (*dirs, *files):
            path = Path(directory, name)
            info = path.lstat()
            if not protected(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                return False
    return True

if system and not safe_checkout():
    # The legacy path is operator-selected. Inspect directory entries only and
    # never run Git, open channel state, or transfer files from this checkout.
    if not (os.path.lexists(target / '.git') and os.path.lexists(target / 'basaltwater.py')):
        refuse('unprotected directory does not resemble a legacy checkout')
    print('legacy')
    sys.exit()

marker = target / '.basaltwater' / 'managed-install'
managed = marker.is_file() and not marker.is_symlink() and marker.read_text() == 'basaltwater-v1\n'
snapshot = system and target == Path('/opt/basaltwater') and (target / '.basaltwater-installation.json').is_file()
data_only = (
    sys.argv[3] == 'cachyos'
    and target == home / '.local/share/basaltwater'
    and (target / 'cachyos-t3').is_dir()
    and not (target / 'cachyos-t3').is_symlink()
    and sorted(path.name for path in target.iterdir()) == ['cachyos-t3']
)
migrated = (
    target.name == 'basaltwater'
    and (target / 'basaltwater.py').is_file()
    and any(target.parent.glob('.basaltwater-migration-*'))
)
if not managed and not snapshot and not data_only and not migrated:
    refuse('unmanaged directory; infra-tools migration requires the intermediate version in docs/migration-guide.md')
print('managed')
EOF
}

INSTALL_PARENT=$(dirname "$INSTALL_DIR")
INSTALL_KIND=$(validate_install_directory)
mkdir -p "$INSTALL_PARENT"
if [ "$SYSTEM_INSTALL" -eq 1 ] && [ "$INSTALL_DIR" = /opt/basaltwater ]; then
    python3 -I - <<'EOF'
from __future__ import annotations

import os
import stat

for name in ('basaltwater-setup-bootstrap.lock', 'basaltwater-setup.lock'):
    path = '/run/lock/' + name
    fd = os.open(path, os.O_NOFOLLOW | os.O_CREAT | os.O_RDONLY | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit(f'Unsafe setup lock: {path}')
EOF
    exec 8</run/lock/basaltwater-setup-bootstrap.lock
    flock --exclusive --nonblock 8 || fail "another Basaltwater setup or installation is running"
    exec 9</run/lock/basaltwater-setup.lock
    flock --exclusive --nonblock 9 || fail "another Basaltwater setup or installation is running"
fi
INSTALL_KIND=$(validate_install_directory)
if [ "$INSTALL_KIND" = legacy ] && [ "$CHANNEL_SET" -eq 0 ]; then
    fail "select --channel explicitly when replacing a writable legacy checkout"
fi
if [ "$SYSTEM_INSTALL" -eq 1 ] && [ "$INSTALL_KIND" != managed ] && [ -z "$SOURCE_COMMIT" ]; then
    case "$CHANNEL" in
        commit-*)
            pinned_commit=${CHANNEL#commit-}
            if [ "${#pinned_commit}" -eq 40 ]; then
                SOURCE_COMMIT=$(printf %s "$pinned_commit" | tr A-F a-f)
            fi
            ;;
    esac
    [ -n "$SOURCE_COMMIT" ] ||
        fail "a new system install or legacy migration requires --source-commit or a full commit channel"
fi

if [ "$CHANNEL_SET" -eq 0 ] && [ -f "$INSTALL_DIR/.basaltwater/channel.json" ]; then
    existing_channel=$(python3 -c \
        'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("channel", ""))' \
        "$INSTALL_DIR/.basaltwater/channel.json" 2>/dev/null || true)
    if [ -n "$existing_channel" ]; then
        CHANNEL=$existing_channel
        validate_channel
    fi
fi

if [ "$INSTALL_KIND" = managed ] && [ -d "$INSTALL_DIR/.git" ]; then
    for data_name in .basaltwater state deployments worktrees cachyos-t3; do
        tracked_data=$(source_git -C "$INSTALL_DIR" ls-files -- "$data_name")
        # The cleanup policy is source-owned; all other files here are private
        # installation state and must never be tracked by the source repository.
        if [ "$data_name" = .basaltwater ] && [ "$tracked_data" = .basaltwater/cleanup.json ]; then
            continue
        fi
        if [ -n "$tracked_data" ]; then
            fail "managed data path is tracked by the source repository: $data_name"
        fi
    done
    existing_changes=$(source_git -C "$INSTALL_DIR" status --porcelain -- . \
        ':(exclude).basaltwater' ':(exclude)state' \
        ':(exclude)cachyos-t3' ':(exclude)deployments' ':(exclude)worktrees')
    policy_changes=$(source_git -C "$INSTALL_DIR" status --porcelain --untracked-files=no -- .basaltwater/cleanup.json)
    if [ -n "$existing_changes$policy_changes" ]; then
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
    if [ "$INSTALL_KIND" = legacy ]; then
        printf 'Writable legacy source quarantined at %s. Retry with the reviewed installer and explicit channel. Staged source: %s\n' "$BACKUP_DIR" "$STAGED_DIR" >&2
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

[ ! -e "$STAGED_DIR" ] && [ ! -L "$STAGED_DIR" ] || fail "staging path already exists: $STAGED_DIR"

printf 'Cloning Basaltwater repository (%s)...\n' "$CHANNEL"
if [ "$SYSTEM_INSTALL" -eq 1 ]; then
    umask 022
fi
if ! source_git clone --no-local --no-checkout -- "$REPOSITORY_URL" "$STAGED_DIR"; then
    fail "could not clone the selected repository"
fi
if ! source_git -C "$STAGED_DIR" fetch --prune --tags origin; then
    fail "could not fetch repository refs"
fi
resolve_channel_ref

# Validate the tree before checkout and before merging installation data.
# Git symlinks and gitlinks may redirect an executable or an import path.
python3 -I - "$STAGED_DIR" "$SYSTEM_INSTALL" "$RESOLVED_COMMIT" <<'EOF'
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

source = Path(sys.argv[1])
environment = dict(os.environ)
if sys.argv[2] == '1':
    environment = {
        'PATH': os.environ['PATH'], 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null',
    }
tree = subprocess.check_output(['git', '--no-replace-objects', '-C', str(source), 'ls-tree', '-rz', sys.argv[3]], env=environment)
reserved = {
    b'state', b'deployments', b'worktrees', b'cachyos-t3', b'agent_payload',
    b'github_runner_payload', b'device_pairing_payload', b'web_panel_payload',
    b'.remote_setup_args.json', b'.basaltwater-installation.json', b'.basaltwater-refresh.json',
}
for entry in filter(None, tree.split(b'\0')):
    mode, _, path = entry.partition(b'\t')
    directory, _, name = path.partition(b'/')
    if directory in reserved or (
        directory == b'.basaltwater' and name != b'cleanup.json'
    ) or (sys.argv[2] == '1' and mode.split()[0] not in (b'100644', b'100755')):
        sys.exit(f'Basaltwater installer: unsafe or reserved source entry {os.fsdecode(path)!r}')
EOF
if ! source_git -C "$STAGED_DIR" checkout --detach "$RESOLVED_COMMIT" >/dev/null; then
    fail "could not check out channel: $CHANNEL"
fi
[ -f "$STAGED_DIR/basaltwater.py" ] || fail "selected source predates Basaltwater; historical releases are unsupported"

[ "$(validate_install_directory)" = "$INSTALL_KIND" ] || fail "install directory changed during staging"
if [ "$INSTALL_KIND" = legacy ]; then
    BACKUP_CONTAINER=$(mktemp -d "$INSTALL_PARENT/.basaltwater-quarantine.XXXXXX")
    BACKUP_DIR="$BACKUP_CONTAINER/source"
elif [ "$INSTALL_KIND" = managed ]; then
    BACKUP_DIR="${INSTALL_DIR}.backup.$(date +%s).$$"
    [ ! -e "$BACKUP_DIR" ] && [ ! -L "$BACKUP_DIR" ] || fail "backup path already exists: $BACKUP_DIR"
fi
# The web service keeps SQLite files open here. Copying while it runs forks
# live state into the backup and silently loses later writes on restart.
# Never stop it automatically: this installer may be running in its terminal.
if [ "$HOST_OS_ID" = cachyos ] && [ -e "$INSTALL_DIR/cachyos-t3/data" ]; then
    python3 - <<'EOF'
from __future__ import annotations
import subprocess
import sys

service = 'basaltwater-cachyos-t3.service'
message = (
    'Basaltwater installer: cannot safely copy T3 web data. From an independent '
    f'KDE terminal, run `systemctl --user stop {service}`, rerun the installer, '
    f'then run `systemctl --user start {service}` if keeping web mode. '
    'The user service state must be inspectable; do not start it during installation.'
)
try:
    result = subprocess.run(
        ['systemctl', '--user', 'show', service, '--property=LoadState,ActiveState,MainPID'],
        capture_output=True, text=True, timeout=15, check=False,
    )
except (OSError, subprocess.TimeoutExpired):
    sys.exit(message)
values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
if (result.returncode not in (0, 4)
        or set(values) != {'LoadState', 'ActiveState', 'MainPID'}
        or values['ActiveState'] not in {'inactive', 'failed'}
        or values['MainPID'] != '0'
        or values['LoadState'] not in {'loaded', 'not-found'}
        or (values['LoadState'] == 'not-found' and values['ActiveState'] != 'inactive')
        or (result.returncode == 4 and values['LoadState'] != 'not-found')):
    sys.exit(message)
EOF
fi
if [ "$INSTALL_KIND" = managed ]; then
    if [ "$SYSTEM_INSTALL" -eq 1 ] && [ -L "$INSTALL_DIR/state" ]; then
        [ "$(readlink "$INSTALL_DIR/state")" = /var/lib/basaltwater ] || fail "unexpected system state link"
        ln -s /var/lib/basaltwater "$STAGED_DIR/state"
    elif [ -d "$INSTALL_DIR/state" ]; then
        cp -a -- "$INSTALL_DIR/state" "$STAGED_DIR/state"
    fi
    if [ -d "$INSTALL_DIR/.basaltwater" ]; then
        mkdir -p "$STAGED_DIR/.basaltwater"
        for state_path in "$INSTALL_DIR/.basaltwater"/* \
            "$INSTALL_DIR/.basaltwater"/.[!.]* "$INSTALL_DIR/.basaltwater"/..?*; do
            [ -e "$state_path" ] || [ -L "$state_path" ] || continue
            case "${state_path##*/}" in cleanup.json|managed-install|system-install|channel.json) continue ;; esac
            cp -a -- "$state_path" "$STAGED_DIR/.basaltwater/"
        done
    fi
    for data_name in deployments worktrees cachyos-t3; do
        if [ -d "$INSTALL_DIR/$data_name" ] && [ ! -L "$INSTALL_DIR/$data_name" ]; then
            cp -a -- "$INSTALL_DIR/$data_name" "$STAGED_DIR/$data_name"
        fi
    done
elif [ "$INSTALL_KIND" = legacy ] && [ "$INSTALL_DIR" = /opt/basaltwater ] &&
    [ -d /var/lib/basaltwater ] && [ ! -L /var/lib/basaltwater ]; then
    ln -s /var/lib/basaltwater "$STAGED_DIR/state"
fi
write_channel_state
printf 'basaltwater-v1\n' > "$STAGED_DIR/.basaltwater/managed-install"
if [ "$SYSTEM_INSTALL" -eq 1 ]; then
    printf 'basaltwater-system-v1\n' > "$STAGED_DIR/.basaltwater/system-install"
    chmod 644 "$STAGED_DIR/.basaltwater/channel.json"
    printf '\n/deployments/\n/worktrees/\n/agent_payload\n/device_pairing_payload\n/web_panel_payload\n/github_runner_payload\n/.remote_setup_args.json\n' >> "$STAGED_DIR/.git/info/exclude"
    [ "$(validate_install_directory "$STAGED_DIR")" = managed ] || fail "staged source ownership is unsafe"
fi

# Set the rollback guard before either rename, including signal boundaries.
ACTIVATION_PENDING=1
if [ -n "$BACKUP_DIR" ]; then
    mv "$INSTALL_DIR" "$BACKUP_DIR"
fi
if ! mv "$STAGED_DIR" "$INSTALL_DIR"; then
    fail "could not activate downloaded source"
fi

ENTRY_SCRIPT="$INSTALL_DIR/basaltwater.py"
printf 'Installing Basaltwater for %s...\n' "$TARGET_USER"
if [ "$(id -u)" -eq 0 ]; then
    if ! run_bootstrap python3 -I -B "$ENTRY_SCRIPT" bootstrap \
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
if [ "$INSTALL_KIND" = managed ] && [ -d "$BACKUP_DIR" ]; then
    chmod 700 "$BACKUP_DIR"
fi
ACTIVATION_PENDING=0
if [ "$SYSTEM_INSTALL" -eq 1 ] && [ "$INSTALL_DIR" = /opt/basaltwater ]; then
    exec 9>&- 8>&-
fi

printf '\nBasaltwater installed successfully.\n'
printf '  Source: %s\n' "$INSTALL_DIR"
printf '  Channel: %s\n' "$CHANNEL"
printf '  Commit: %s\n' "$RESOLVED_COMMIT"
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
    if [ "$HOST_OS_ID" = cachyos ] || [ "$SYSTEM_INSTALL" -eq 0 ]; then
        run_for_target python3 "$ENTRY_SCRIPT" setup "$@"
        return
    fi
    if [ -t 2 ] && [ -r /dev/tty ]; then
        run_privileged env \
            USER="$TARGET_USER" \
            SUDO_USER="$TARGET_USER" \
            python3 -I -B "$ENTRY_SCRIPT" setup "$@" < /dev/tty
    else
        run_privileged env \
            USER="$TARGET_USER" \
            SUDO_USER="$TARGET_USER" \
            python3 -I -B "$ENTRY_SCRIPT" setup "$@"
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
