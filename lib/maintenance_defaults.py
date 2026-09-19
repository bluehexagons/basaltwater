"""Shared defaults for maintenance and cleanup tasks."""

from __future__ import annotations

from lib.types import BYTES_PER_GB

JOURNAL_MAX_USE = "100M"
JOURNAL_MAX_AGE = "30d"
STALE_CRASH_REPORT_MAX_AGE_DAYS = 30
CRASH_REPORT_DIRS = ("/var/crash", "/var/lib/systemd/coredump")
CRASH_REPORT_PATTERNS = (
    r"core\..+",
    r".+\.(?:crash|upload|uploaded)",
)
STORAGE_WARNING_PERCENT = 80
STORAGE_CRITICAL_PERCENT = 90

# Wait up to 5 minutes for apt/dpkg locks to be released before failing.
APT_LOCK_OPTIONS = [
    "-o", "DPkg::Lock::Timeout=300",
]

# apt-get update otherwise permits some failed indexes and stale metadata.
APT_UPDATE_OPTIONS = ["-o", "APT::Update::Error-Mode=any"]

# Individual cleanup commands should never block the whole maintenance run forever.
CLEANUP_COMMAND_TIMEOUT_SECONDS = 600

# User-scoped cache maintenance runs independently from privileged host
# cleanup. Pruning commands run daily, while full cache eviction is
# reserved for these bounded size/age thresholds or low filesystem headroom.
USER_CACHE_FREE_MIN_BYTES = 2 * BYTES_PER_GB
USER_CACHE_FREE_MAX_BYTES = 8 * BYTES_PER_GB
USER_CACHE_PRESSURE_MIN_BYTES = BYTES_PER_GB // 16
NPM_CACHE_MAX_BYTES = 2 * BYTES_PER_GB
NPM_NPX_CACHE_MAX_BYTES = BYTES_PER_GB
PIP_CACHE_MAX_BYTES = 2 * BYTES_PER_GB
GO_BUILD_CACHE_MAX_BYTES = 2 * BYTES_PER_GB
GO_MODULE_CACHE_MAX_BYTES = 5 * BYTES_PER_GB
OPENCODE_CACHE_MAX_BYTES = 2 * BYTES_PER_GB
CODEX_CACHE_MAX_BYTES = BYTES_PER_GB
STALE_USER_TOOL_CACHE_MAX_AGE_DAYS = 90
STALE_USER_TOOL_TMP_MAX_AGE_DAYS = 7
STALE_NPX_CACHE_MAX_AGE_DAYS = 30
T3_ROTATED_LOG_MAX_BYTES = BYTES_PER_GB // 4
T3_ROTATED_LOG_MAX_AGE_DAYS = 14

# Remove basaltwater-owned temp artifacts after a week. These are normally
# cleaned up by finally blocks, but interrupted setup/deploy/provision runs can
# leave them behind.
#
STALE_BASALTWATER_TMP_MAX_AGE_DAYS = 7
BASALTWATER_TMP_PATTERNS = (
    r"basaltwater_setup_build_[A-Za-z0-9_-]+",
    r"basaltwater_recall_[A-Za-z0-9_-]+",
    r"basaltwater_deploy_[A-Za-z0-9_-]+",
    r"basaltwater_pubkey\.[A-Za-z0-9]+",
    r"antistatic-(?:server|db)-linux-(?:amd64|arm64)\.v[A-Za-z0-9._-]+",
    # Clean up temp directories left by interrupted deployments from releases
    # that still managed Bundler. This does not install or update Ruby.
    r"bundler\d{8}-\d+-[A-Za-z0-9_-]+",
)
# Build tools use /var/tmp to avoid filling small tmpfs-backed /tmp partitions.
BASALTWATER_TMP_DIRS = ("/tmp", "/var/tmp")
