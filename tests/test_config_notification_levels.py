"""Configuration and delivery tests for outbound notification levels."""

from __future__ import annotations

import argparse
import io
import logging
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from lib.arg_parser import add_setup_arguments
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from lib.notifications import (
    Notification,
    NotificationConfig,
    NotificationSender,
    load_notification_configs_from_state,
    parse_notification_args,
)
from lib.runtime_config import RuntimeConfig


def _setup_config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "server.example",
        "username": "admin",
        "system_type": "server_lite",
    }
    values.update(overrides)
    return SetupConfig(**values)


class TestNotificationLevelCli(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = argparse.ArgumentParser()
        add_setup_arguments(self.parser)

    def test_level_is_optional_and_accepts_supported_value(self) -> None:
        omitted = self.parser.parse_args(["server.example"])
        selected = self.parser.parse_args(
            ["server.example", "--notification-level", "warning"]
        )

        self.assertIsNone(omitted.notification_level)
        self.assertEqual(selected.notification_level, "warning")

    def test_invalid_level_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser.parse_args(
                ["server.example", "--notification-level", "debug"]
            )


class TestNotificationLevelConfig(unittest.TestCase):
    def test_level_round_trips_through_setup_state_and_commands(self) -> None:
        config = _setup_config(notification_level="warning")

        self.assertIn("--notification-level warning", config.to_remote_args())
        self.assertIn("--notification-level warning", config.to_setup_command())
        self.assertEqual(config.to_dict()["notification_level"], "warning")

        restored = SetupConfig.from_dict(
            config.host,
            config.system_type,
            dict(config.to_dict()),
        )
        self.assertEqual(restored.notification_level, "warning")

    def test_patch_omission_preserves_level_and_explicit_value_changes_it(self) -> None:
        cached = _setup_config(notification_level="warning")

        preserved = merge_setup_configs(cached, _setup_config())
        changed = merge_setup_configs(
            cached,
            _setup_config(notification_level="error"),
        )

        self.assertEqual(preserved.notification_level, "warning")
        self.assertEqual(changed.notification_level, "error")

    def test_invalid_programmatic_level_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Notification level must be"):
            _setup_config(notification_level="debug")

    def test_runtime_config_keeps_saved_level(self) -> None:
        runtime = RuntimeConfig.from_setup_config(
            _setup_config(notification_level="off")
        )

        self.assertEqual(runtime.notification_level, "off")
        self.assertEqual(runtime.to_dict()["notification_level"], "off")


class TestNotificationLevelDelivery(unittest.TestCase):
    def test_delivery_matrix(self) -> None:
        cases = (
            ("normal", "always", "good", "success", True),
            ("normal", "signal", "good", "success", False),
            ("verbose", "signal", "good", "success", True),
            ("warning", "always", "good", "success", False),
            ("warning", "always", "warning", "firing", True),
            ("warning", "always", "info", "resolved", True),
            ("error", "always", "warning", "firing", False),
            ("error", "always", "error", "firing", True),
            ("error", "always", "info", "resolved", True),
            ("off", "always", "error", "firing", False),
        )

        for level, policy, status, state, expected in cases:
            with self.subTest(
                level=level,
                policy=policy,
                status=status,
                state=state,
            ):
                sender = NotificationSender(
                    [
                        NotificationConfig(
                            type="webhook",
                            target="https://example.com/hook",
                            level=level,
                        )
                    ]
                )
                notification = Notification(
                    subject="Test",
                    job="test",
                    status=status,
                    message="Test event",
                    state=state,
                    delivery_policy=policy,
                )
                with patch.object(sender, "_send_webhook") as send_webhook:
                    self.assertTrue(sender.send(notification))

                self.assertEqual(send_webhook.called, expected)

    def test_parser_applies_one_level_to_every_target(self) -> None:
        configs = parse_notification_args(
            [
                ["webhook", "https://example.com/hook"],
                ["mailbox", "ops@example.com"],
            ],
            notification_level="verbose",
        )

        self.assertEqual([config.level for config in configs], ["verbose", "verbose"])

    def test_suppression_diagnostic_is_quiet_at_info_level(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.notification-level.suppression")
        logger.handlers = []
        logger.propagate = False
        logger.addHandler(logging.StreamHandler(stream))
        logger.setLevel(logging.INFO)
        sender = NotificationSender(
            [
                NotificationConfig(
                    type="webhook",
                    target="https://example.com/hook",
                    level="warning",
                )
            ],
            logger=logger,
        )

        self.assertTrue(
            sender.send(
                Notification(
                    subject="Routine success",
                    job="test",
                    status="good",
                    message="Nothing requires attention",
                )
            )
        )
        self.assertNotIn("Notification suppressed", stream.getvalue())

    @patch(
        "lib.machine_state.load_notification_state",
        return_value={
            "notify_specs": [["mailbox", "ops@example.com"]],
            "notification_level": "warning",
        },
    )
    def test_saved_level_is_loaded_for_scheduled_jobs(self, _load_state) -> None:
        configs = load_notification_configs_from_state()

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].level, "warning")

    @patch(
        "lib.machine_state.load_notification_state",
        return_value={
            "notify_specs": [["mailbox", "ops@example.com"]],
            "notification_level": "corrupt",
        },
    )
    def test_corrupt_saved_level_falls_back_to_normal(self, _load_state) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.notification-level.invalid-state")
        logger.handlers = []
        logger.propagate = False
        logger.addHandler(logging.StreamHandler(stream))
        logger.setLevel(logging.INFO)

        configs = load_notification_configs_from_state(logger)

        self.assertEqual(configs[0].level, "normal")
        self.assertIn("Invalid saved notification level", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
