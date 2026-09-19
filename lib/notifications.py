"""Centralized notification system for basaltwater.

Supports webhook and email notifications for important events.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from logging import DEBUG, ERROR, INFO, Logger, WARNING
from typing import Any, Literal, Optional, cast
from uuid import uuid4
import urllib.request
import urllib.error
import urllib.parse

from lib.logging_utils import log_event
from lib.types import JSONDict

NotificationStatus = Literal["good", "info", "warning", "error"]
NotificationState = Literal["firing", "resolved", "success"]
NotificationDeliveryPolicy = Literal["always", "signal"]
NotificationLevel = Literal["verbose", "normal", "warning", "error", "off"]

NOTIFICATION_LEVELS: tuple[NotificationLevel, ...] = (
    "verbose",
    "normal",
    "warning",
    "error",
    "off",
)
DEFAULT_NOTIFICATION_LEVEL: NotificationLevel = "normal"

NETWORK_TIMEOUT_SECONDS = 30
NOTIFICATION_SCHEMA_VERSION = 2
_MAILBOX_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BEARER_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_WEBHOOK_RETRY_DELAYS_SECONDS = (1.0, 3.0)
_WEBHOOK_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_MAX_RETRY_AFTER_SECONDS = 30


@dataclass
class NotificationConfig:
    """Configuration for a notification target."""
    
    type: Literal["webhook", "mailbox"]
    target: str = field(repr=False)
    level: NotificationLevel = DEFAULT_NOTIFICATION_LEVEL
    strict_https: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.target, str):
            self.target = self.target.strip()
        self.level = normalize_notification_level(self.level)
        self.strict_https = normalize_notification_strict_https(self.strict_https)
    
    def __str__(self) -> str:
        return f"{self.type}:{notification_target_summary(self.type, self.target)}"
    
    @classmethod
    def from_string(cls, config_str: str) -> NotificationConfig:
        """Parse notification config from string format."""
        parts = config_str.split(":", 1)
        if len(parts) != 2:
            raise ValueError("Invalid notification config format")
        
        notif_type, target = parts
        if notif_type not in ["webhook", "mailbox"]:
            raise ValueError(f"Invalid notification type: {notif_type}")

        config = cls(
            type=cast(Literal["webhook", "mailbox"], notif_type),
            target=target.strip(),
        )
        validate_notification_config(config)
        return config


@dataclass
class Notification:
    """A notification message."""
    
    subject: str
    job: str
    status: NotificationStatus
    message: str
    details: Optional[str] = None
    hostname: str = field(default_factory=lambda: socket.gethostname())
    data: Optional[JSONDict] = None
    event_type: Optional[str] = None
    state: Optional[NotificationState] = None
    dedup_key: Optional[str] = None
    actions: Optional[list[str]] = None
    delivery_policy: NotificationDeliveryPolicy = "always"
    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
    )

    def __post_init__(self) -> None:
        """Fill the common event fields used by every notification target."""
        if self.event_type is None:
            self.event_type = self.job
        if self.state is None:
            self.state = "success" if self.status in ("good", "info") else "firing"
        if not isinstance(self.event_id, str) or not _EVENT_ID_PATTERN.fullmatch(
            self.event_id
        ):
            raise ValueError("Notification event ID is invalid")
        if not isinstance(self.occurred_at, str) or len(self.occurred_at) > 64:
            raise ValueError("Notification occurrence time is invalid")
        try:
            occurred_time = datetime.fromisoformat(self.occurred_at)
        except ValueError as exc:
            raise ValueError("Notification occurrence time is invalid") from exc
        if occurred_time.tzinfo is None or occurred_time.utcoffset() is None:
            raise ValueError("Notification occurrence time is invalid")

    def to_dict(self) -> JSONDict:
        """Return a descriptive REST payload with separate event and operator data."""
        return {
            "schema_version": NOTIFICATION_SCHEMA_VERSION,
            "event": {
                "id": self.event_id,
                "occurred_at": self.occurred_at,
                "type": self.event_type,
                "state": self.state,
                "status": self.status,
                "deduplication_key": self.dedup_key,
            },
            "operator": {
                "subject": self.subject,
                "job": self.job,
                "system": self.hostname,
                "what_happened": self.message,
                "suggested_actions": list(self.actions or []),
                "details": self.details or "",
            },
            "data": self.data or {},
        }


class NotificationSender:
    """Handles sending notifications to configured targets."""
    
    def __init__(self, configs: list[NotificationConfig], logger: Optional[Logger] = None):
        """Initialize notification sender.
        
        Args:
            configs: List of notification configurations
            logger: Optional logger for debugging
        """
        self.configs = configs
        self.logger = logger
    
    def send(self, notification: Notification) -> bool:
        """Send notification to all configured targets.
        
        Returns:
            True only if ALL configured targets were sent successfully.
            Returns True if no targets are configured (nothing to fail).
        """
        if not self.configs:
            return True

        all_succeeded = True
        eligible_count = 0
        suppressed_levels: set[NotificationLevel] = set()
        for config in self.configs:
            try:
                validate_notification_config(config)
                if not _should_deliver(notification, config.level):
                    suppressed_levels.add(config.level)
                    continue
                eligible_count += 1
                if config.type == "webhook":
                    self._send_webhook(
                        config.target,
                        notification,
                        strict_https=config.strict_https,
                    )
                elif config.type == "mailbox":
                    self._send_mailbox(config.target, notification)
            except Exception as e:
                all_succeeded = False
                if self.logger:
                    log_event(
                        self.logger,
                        "Notification delivery failed",
                        level=ERROR,
                        job=notification.job,
                        notification_type=config.type,
                        target=_redact_notification_target(config),
                        error=str(e),
                    )

        if eligible_count == 0 and suppressed_levels and self.logger:
            log_event(
                self.logger,
                "Notification suppressed by notification level",
                level=DEBUG,
                job=notification.job,
                event_type=notification.event_type,
                status=notification.status,
                state=notification.state,
                notification_levels=",".join(sorted(suppressed_levels)),
            )
        
        return all_succeeded
    
    def _send_webhook(
        self,
        url: str,
        notification: Notification,
        *,
        strict_https: bool = False,
    ) -> None:
        """Send webhook notification via HTTP POST."""
        request_url, bearer_token = _webhook_request_target(url)
        data = json.dumps(notification.to_dict()).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'basaltwater-notification/1.0',
            'X-Basaltwater-Event-ID': notification.event_id,
        }
        if bearer_token is not None:
            headers['Authorization'] = f'Bearer {bearer_token}'

        attempts = len(_WEBHOOK_RETRY_DELAYS_SECONDS) + 1
        for attempt in range(attempts):
            request = urllib.request.Request(
                request_url,
                data=data,
                headers=headers,
                method='POST',
            )
            try:
                with _open_webhook_request(
                    request,
                    strict_https=strict_https,
                ) as response:
                    status = int(response.status)
                    if not 200 <= status < 300:
                        raise urllib.error.HTTPError(
                            request_url,
                            status,
                            f"HTTP {status}",
                            getattr(response, "headers", None),
                            response,
                        )
                if self.logger:
                    log_event(
                        self.logger,
                        "Webhook notification sent",
                        level=INFO,
                        job=notification.job,
                        status=notification.status,
                        target=_redact_notification_target(
                            NotificationConfig(type="webhook", target=url)
                        ),
                        attempts=attempt + 1,
                    )
                return
            except urllib.error.HTTPError as exc:
                exc.close()
                if (
                    exc.code not in _WEBHOOK_RETRYABLE_STATUS_CODES
                    or attempt == attempts - 1
                ):
                    raise RuntimeError(
                        f"Webhook returned HTTP {exc.code}"
                    ) from exc
                failure = f"HTTP {exc.code}"
                delay = _webhook_retry_delay(exc, attempt)
            except (urllib.error.URLError, TimeoutError) as exc:
                reason = getattr(exc, "reason", exc)
                if (
                    isinstance(reason, ssl.SSLError)
                    or attempt == attempts - 1
                ):
                    raise RuntimeError(
                        "Webhook connection failed: "
                        + _webhook_connection_error(reason)
                    ) from exc
                failure = _webhook_connection_error(reason)
                delay = _WEBHOOK_RETRY_DELAYS_SECONDS[attempt]

            if self.logger:
                log_event(
                    self.logger,
                    "Webhook notification retry scheduled",
                    level=WARNING,
                    job=notification.job,
                    target=_redact_notification_target(
                        NotificationConfig(type="webhook", target=url)
                    ),
                    failed_attempt=attempt + 1,
                    reason=failure,
                    delay_seconds=delay,
                )
            time.sleep(delay)
    
    def _send_mailbox(self, email: str, notification: Notification) -> None:
        """Send email notification."""
        body_parts = [
            f"[{notification.status.upper()}] {notification.subject}",
            f"Job: {notification.job}",
            f"Status: {notification.status.upper()}",
            f"System: {notification.hostname}",
            f"Event: {notification.event_type}",
            f"State: {notification.state}",
            f"Event ID: {notification.event_id}",
            f"Occurred: {notification.occurred_at}",
            "",
            "What happened:",
            notification.message,
        ]
        if notification.actions:
            body_parts.extend([
                "",
                "Suggested action:",
                *[f"- {action}" for action in notification.actions],
            ])
        if notification.details:
            body_parts.extend(["", "Details:", notification.details])
        if notification.dedup_key:
            body_parts.extend(["", f"Deduplication key: {notification.dedup_key}"])
        if notification.data:
            body_parts.extend([
                "",
                "Machine-readable event data (JSON):",
                json.dumps(notification.data, indent=2, sort_keys=True),
            ])
        body_parts.extend([
            "",
            "---",
            "This is an automated notification from basaltwater.",
            "Check system logs for detailed information.",
            "",
        ])
        body = "\n".join(body_parts)
        
        try:
            subprocess.run(
                ['mail', '-s', notification.subject, email],
                input=body.encode('utf-8'),
                check=True,
                capture_output=True,
                timeout=NETWORK_TIMEOUT_SECONDS
            )
            if self.logger:
                log_event(
                    self.logger,
                    "Mailbox notification sent",
                    level=INFO,
                    job=notification.job,
                    status=notification.status,
                    target=_redact_notification_target(
                        NotificationConfig(type="mailbox", target=email)
                    ),
                )
                    
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise Exception(f"Failed to send email: {e}")


def send_notification(
    configs: list[NotificationConfig],
    subject: str,
    job: str,
    status: NotificationStatus,
    message: str,
    details: Optional[str] = None,
    logger: Optional[Logger] = None,
    data: Optional[JSONDict] = None,
    event_type: Optional[str] = None,
    state: Optional[NotificationState] = None,
    dedup_key: Optional[str] = None,
    actions: Optional[list[str]] = None,
    delivery_policy: NotificationDeliveryPolicy = "always",
) -> bool:
    """Send a notification to configured targets."""
    notification = Notification(
        subject=subject,
        job=job,
        status=status,
        message=message,
        details=details,
        data=data,
        event_type=event_type,
        state=state,
        dedup_key=dedup_key,
        actions=actions,
        delivery_policy=delivery_policy,
    )
    
    sender = NotificationSender(configs, logger=logger)
    return sender.send(notification)


def send_notification_safe(
    configs: list[NotificationConfig],
    subject: str,
    job: str,
    status: NotificationStatus,
    message: str,
    details: Optional[str] = None,
    logger: Optional[Logger] = None,
    data: Optional[JSONDict] = None,
    event_type: Optional[str] = None,
    state: Optional[NotificationState] = None,
    dedup_key: Optional[str] = None,
    actions: Optional[list[str]] = None,
    delivery_policy: NotificationDeliveryPolicy = "always",
) -> bool:
    """Send a notification and suppress delivery errors."""
    if not configs:
        return True
    try:
        notification_kwargs: dict[str, object] = {
            "subject": subject,
            "job": job,
            "status": status,
            "message": message,
            "details": details,
            "logger": logger,
            "event_type": event_type,
            "state": state,
            "dedup_key": dedup_key,
            "actions": actions,
            "delivery_policy": delivery_policy,
        }
        if data is not None:
            notification_kwargs["data"] = data
        delivered = send_notification(configs, **notification_kwargs)  # type: ignore[arg-type]
        if delivered is False and logger:
            log_event(
                logger,
                "Notification delivery incomplete",
                level=WARNING,
                job=job,
            )
        return delivered
    except Exception as e:
        if logger:
            log_event(
                logger,
                "Notification send suppressed after delivery failure",
                level=WARNING,
                job=job,
                error=str(e),
            )
        return False


def parse_notification_args(
    notify_args: Optional[list[list[str]]],
    notification_level: object = None,
    strict_https: object = None,
) -> list[NotificationConfig]:
    """Parse notification arguments from command line."""
    if not notify_args:
        return []

    level = normalize_notification_level(notification_level)
    strict_tls = normalize_notification_strict_https(strict_https)
    
    configs: list[NotificationConfig] = []
    seen: set[tuple[str, str]] = set()
    for notify_arg in notify_args:
        if not isinstance(notify_arg, (list, tuple)) or len(notify_arg) != 2:
            continue
        
        notif_type, target = notify_arg
        if notif_type not in ["webhook", "mailbox"] or not isinstance(target, str):
            continue
        config = NotificationConfig(
            type=cast(Literal["webhook", "mailbox"], notif_type),
            target=target.strip(),
            level=level,
            strict_https=strict_tls,
        )
        try:
            validate_notification_config(config)
        except ValueError:
            continue
        identity = (config.type, config.target)
        if identity in seen:
            continue
        seen.add(identity)
        configs.append(config)
    
    return configs


def validate_notification_config(config: NotificationConfig) -> None:
    """Validate a parsed notification target."""

    if (
        not isinstance(config.type, str)
        or config.type not in {"webhook", "mailbox"}
        or not isinstance(config.target, str)
    ):
        raise ValueError("Invalid notification configuration")
    normalize_notification_level(config.level)
    normalize_notification_strict_https(config.strict_https)
    target = config.target.strip()
    if not target:
        raise ValueError(f"Notification target for {config.type} must not be empty")

    if config.type == "webhook":
        try:
            _webhook_request_target(target)
        except ValueError:
            raise ValueError("Invalid webhook URL")
        return

    if config.type == "mailbox":
        if target.startswith("-") or not _MAILBOX_PATTERN.fullmatch(target):
            raise ValueError("Invalid mailbox address")
        return

    raise ValueError(f"Invalid notification type: {config.type}")


def _webhook_request_target(target: str) -> tuple[str, str | None]:
    """Return the request URL and optional fragment-carried bearer token."""

    if (
        not isinstance(target, str)
        or len(target) > 4096
        or any(ord(character) <= 32 or ord(character) == 127 for character in target)
    ):
        raise ValueError("Webhook URL is too long")
    try:
        parsed = urllib.parse.urlsplit(target)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Webhook URL is malformed") from exc
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("Webhook URL is malformed")
    bearer_token = parsed.fragment or None
    if bearer_token is not None:
        if parsed.scheme != "https" or not _BEARER_TOKEN_PATTERN.fullmatch(
            bearer_token
        ):
            raise ValueError("Webhook bearer token is invalid")
    return parsed._replace(fragment="").geturl(), bearer_token


def validate_notification_args(notify_args: Optional[list[list[str]]]) -> None:
    """Validate raw --notify argument pairs from CLI/config surfaces."""

    if not notify_args:
        return
    if not isinstance(notify_args, list):
        raise ValueError("--notify configuration must be a list")

    for notify_arg in notify_args:
        if not isinstance(notify_arg, (list, tuple)) or len(notify_arg) != 2:
            raise ValueError(
                "--notify requires TYPE and TARGET"
            )
        notif_type, target = notify_arg
        if (
            not isinstance(notif_type, str)
            or notif_type not in ["webhook", "mailbox"]
        ):
            raise ValueError(f"Invalid notification type: {notif_type}")
        if not isinstance(target, str):
            raise ValueError(f"Notification target for {notif_type} must be text")
        validate_notification_config(
            NotificationConfig(type=cast(Literal["webhook", "mailbox"], notif_type), target=target)
        )


def normalize_notification_level(value: object) -> NotificationLevel:
    """Return a validated notification level, applying the default when omitted."""

    if value is None:
        return DEFAULT_NOTIFICATION_LEVEL
    if not isinstance(value, str) or value not in NOTIFICATION_LEVELS:
        raise ValueError(
            "Notification level must be one of: "
            + ", ".join(NOTIFICATION_LEVELS)
        )
    return cast(NotificationLevel, value)


def notification_level_from_state(
    value: object,
    logger: Optional[Logger] = None,
) -> NotificationLevel:
    """Load a saved level without letting corrupt state disable notifications."""

    try:
        return normalize_notification_level(value)
    except ValueError as exc:
        if logger:
            log_event(
                logger,
                "Invalid saved notification level; using default",
                level=WARNING,
                error=str(exc),
                notification_level=DEFAULT_NOTIFICATION_LEVEL,
            )
        return DEFAULT_NOTIFICATION_LEVEL


def normalize_notification_strict_https(value: object) -> bool:
    """Return whether HTTPS notification webhooks must verify certificates."""

    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError("Strict notification HTTPS setting must be boolean")
    return value


def notification_strict_https_from_state(
    value: object,
    logger: Optional[Logger] = None,
) -> bool:
    """Load the saved strict HTTPS setting without disabling notifications."""

    try:
        return normalize_notification_strict_https(value)
    except ValueError as exc:
        if logger:
            log_event(
                logger,
                "Invalid saved notification HTTPS setting; using default",
                level=WARNING,
                error=str(exc),
                notification_strict_https=False,
            )
        return False


def load_notification_configs_from_state(logger: Optional[Logger] = None) -> list[NotificationConfig]:
    """Load notification configs from saved machine state.
    
    This helper loads notification configurations that were previously saved during
    setup, allowing service tools to use the same notification targets without
    re-parsing command-line arguments.
    
    Args:
        logger: Optional logger for debugging
    
    Returns:
        List of NotificationConfig objects, empty list if state not found or parsing fails
    
    Example:
        # In a service tool (e.g., auto_update_node.py)
        configs = load_notification_configs_from_state(logger)
        sender = NotificationSender(configs, logger=logger)
    """
    try:
        from lib.machine_state import load_notification_state
        setup_config = load_notification_state()
        if setup_config and 'notify_specs' in setup_config:
            notify_specs = setup_config['notify_specs']
            if not isinstance(notify_specs, list):
                raise ValueError("Saved notification targets must be a list")
            notification_level = notification_level_from_state(
                setup_config.get("notification_level"),
                logger,
            )
            strict_https = notification_strict_https_from_state(
                setup_config.get("notification_strict_https"),
                logger,
            )
            configs: list[NotificationConfig] = []
            seen: set[tuple[str, str]] = set()
            invalid_count = 0
            for notify_spec in notify_specs:
                parsed = parse_notification_args(
                    [notify_spec],
                    notification_level=notification_level,
                    strict_https=strict_https,
                )
                if not parsed:
                    invalid_count += 1
                    continue
                config = parsed[0]
                identity = (config.type, config.target)
                if identity not in seen:
                    seen.add(identity)
                    configs.append(config)
            if invalid_count and logger:
                log_event(
                    logger,
                    "Failed to load notification configs from machine state",
                    level=WARNING,
                    error=f"Ignored {invalid_count} invalid target(s)",
                )
            return configs
    except (ImportError, OSError, ValueError, KeyError, TypeError) as e:
        if logger:
            log_event(
                logger,
                "Failed to load notification configs from machine state",
                level=WARNING,
                error=str(e),
            )
    
    return []


def send_setup_notification(
    notify_specs: Optional[list[list[str]]],
    system_type: str,
    host: str,
    success: bool,
    errors: Optional[list[str]] = None,
    friendly_name: Optional[str] = None,
    logger: Optional[Logger] = None,
    notification_level: object = None,
    strict_https: object = None,
) -> bool:
    """Send a notification summarizing setup results.

    Args:
        notify_specs: Raw notify specs from SetupConfig (list of [type, target] pairs)
        system_type: The system type that was set up
        host: The host that was set up
        success: Whether setup completed successfully
        errors: Optional list of error messages encountered during setup
        friendly_name: Optional human-readable name for this system
        logger: Optional logger for debugging
        notification_level: Delivery threshold for this system
        strict_https: Require certificate and hostname verification for HTTPS
            webhook targets

    Returns:
        True if all notifications were sent successfully, False otherwise
    """
    configs = parse_notification_args(
        notify_specs,
        notification_level=notification_level,
        strict_https=strict_https,
    )
    if not configs:
        return True

    # Build a descriptive identifier: prefer friendly_name, fall back to host
    identifier = f"{friendly_name} ({host})" if friendly_name else host

    if success:
        status: NotificationStatus = "good"
        subject = f"Setup complete: {system_type} on {identifier}"
        message = f"Setup of {system_type} on {identifier} completed successfully."
    else:
        status = "error"
        subject = f"Setup failed: {system_type} on {identifier}"
        message = f"Setup of {system_type} on {identifier} failed."

    details_parts = [f"System type: {system_type}", f"Host: {host}"]
    if friendly_name:
        details_parts.append(f"Name: {friendly_name}")
    if errors:
        details_parts.append(f"\nErrors ({len(errors)}):")
        for error in errors:
            details_parts.append(f"  - {error}")
    details = "\n".join(details_parts)

    return send_notification(
        configs,
        subject=subject,
        job="setup",
        status=status,
        message=message,
        details=details,
        logger=logger,
        event_type="setup",
        state="success" if success else "firing",
        dedup_key=f"setup:{system_type}:{host}",
    )


def _should_deliver(
    notification: Notification,
    notification_level: object = None,
) -> bool:
    """Return whether an event passes the system's outbound notification level."""

    level = normalize_notification_level(notification_level)
    if level == "off":
        return False
    if level == "verbose":
        return True
    if level == "error":
        return notification.status == "error" or notification.state == "resolved"
    if level == "warning":
        return notification.status in ("warning", "error") or notification.state in (
            "firing",
            "resolved",
        )
    if notification.delivery_policy == "always":
        return True
    return notification.status in ("warning", "error") or notification.state in (
        "firing",
        "resolved",
    )


def _redact_notification_target(config: NotificationConfig) -> str:
    """Return a log-safe summary of a notification target."""

    return notification_target_summary(config.type, config.target)


def notification_target_summary(notification_type: str, target: str) -> str:
    """Return a display-safe target without endpoint paths or credentials."""

    if notification_type == "webhook":
        try:
            request_url, _bearer_token = _webhook_request_target(target)
            parsed = urllib.parse.urlsplit(request_url)
            port = parsed.port
        except (TypeError, ValueError):
            return "unknown webhook"
        if not parsed.hostname:
            return "unknown webhook"
        host = (
            f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        )
        port_suffix = f":{port}" if port is not None else ""
        scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "webhook"
        return f"{scheme}://{host}{port_suffix}"

    if (
        notification_type == "mailbox"
        and isinstance(target, str)
        and not target.startswith("-")
        and _MAILBOX_PATTERN.fullmatch(target)
    ):
        _local_part, domain = target.rsplit("@", 1)
        return f"*@{domain}"

    if notification_type == "mailbox":
        return "unknown mailbox"
    return "unknown notification target"


class _RejectWebhookRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent POST redirects from forwarding notification credentials."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _open_webhook_request(
    request: urllib.request.Request,
    *,
    strict_https: bool = False,
) -> Any:
    handlers: list[Any] = [_RejectWebhookRedirects()]
    if request.type == "https":
        handlers.append(
            urllib.request.HTTPSHandler(
                context=_webhook_ssl_context(strict_https),
            )
        )
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS)


def _webhook_ssl_context(strict_https: bool) -> ssl.SSLContext:
    """Return the configured TLS policy for an HTTPS webhook."""

    if strict_https:
        return ssl.create_default_context()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _webhook_retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if isinstance(retry_after, str) and retry_after.isdigit():
        return float(min(int(retry_after), _MAX_RETRY_AFTER_SECONDS))
    return _WEBHOOK_RETRY_DELAYS_SECONDS[attempt]


def _webhook_connection_error(reason: object) -> str:
    if isinstance(reason, OSError) and reason.strerror:
        return f"{type(reason).__name__}: {reason.strerror}"
    return type(reason).__name__
