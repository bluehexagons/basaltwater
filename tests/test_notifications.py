"""Tests for lib/notifications.py: config parsing, notification objects, sender logic."""

from __future__ import annotations

import io
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import unittest
import urllib.request
from email.message import Message
from urllib.error import HTTPError, URLError
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.notifications import (
    NotificationConfig,
    Notification,
    NotificationSender,
    _RejectWebhookRedirects,
    _open_webhook_request,
    load_notification_configs_from_state,
    notification_target_summary,
    parse_notification_args,
    send_notification_safe,
    send_setup_notification,
    validate_notification_args,
)


class TestNotificationConfig(unittest.TestCase):
    def test_from_string_webhook(self):
        config = NotificationConfig.from_string('webhook:https://example.com/hook')
        self.assertEqual(config.type, 'webhook')
        self.assertEqual(config.target, 'https://example.com/hook')

    def test_from_string_mailbox(self):
        config = NotificationConfig.from_string('mailbox:admin@example.com')
        self.assertEqual(config.type, 'mailbox')
        self.assertEqual(config.target, 'admin@example.com')

    def test_from_string_invalid_type(self):
        with self.assertRaises(ValueError):
            NotificationConfig.from_string('sms:+1234567890')

    def test_from_string_no_colon(self):
        with self.assertRaises(ValueError):
            NotificationConfig.from_string('invalid')

    def test_string_and_repr_redact_sensitive_webhook_components(self):
        token = "a" * 43
        config = NotificationConfig(
            type='webhook',
            target=f'https://hooks.example.com/private/token?secret=yes#{token}',
        )

        self.assertEqual(str(config), 'webhook:https://hooks.example.com')
        self.assertNotIn('private', repr(config))
        self.assertNotIn('secret', repr(config))
        self.assertNotIn(token, repr(config))

    def test_target_summary_keeps_only_routing_safe_components(self):
        self.assertEqual(
            notification_target_summary(
                "webhook",
                "https://[2001:db8::20]:9443/private?token=secret",
            ),
            "https://[2001:db8::20]:9443",
        )
        self.assertEqual(
            notification_target_summary("mailbox", "operator@example.com"),
            "*@example.com",
        )
        self.assertEqual(
            notification_target_summary(
                "mailbox",
                "operator@example.com\nInjected: value",
            ),
            "unknown mailbox",
        )
        self.assertEqual(
            notification_target_summary(
                "webhook",
                "https://operator:secret@hooks.example.com/ingest",
            ),
            "unknown webhook",
        )


class TestNotification(unittest.TestCase):
    def test_to_dict(self):
        n = Notification(subject='Test', job='sync', status='good', message='All ok')
        d = n.to_dict()
        self.assertEqual(d['schema_version'], 2)
        self.assertEqual(d['event']['type'], 'sync')
        self.assertRegex(d['event']['id'], r'^[a-f0-9]{32}$')
        self.assertTrue(d['event']['occurred_at'].endswith('+00:00'))
        self.assertEqual(d['event']['state'], 'success')
        self.assertEqual(d['event']['status'], 'good')
        self.assertEqual(d['operator']['subject'], 'Test')
        self.assertEqual(d['operator']['job'], 'sync')
        self.assertEqual(d['operator']['what_happened'], 'All ok')
        self.assertEqual(d['operator']['suggested_actions'], [])
        self.assertEqual(d['operator']['details'], '')
        self.assertEqual(d['data'], {})

    def test_to_dict_includes_empty_details_when_absent(self):
        n = Notification(subject='Test', job='sync', status='good', message='ok', details=None)
        d = n.to_dict()
        self.assertEqual(d['operator']['details'], '')

    def test_rejects_invalid_custom_event_identity_and_occurrence_time(self):
        with self.assertRaisesRegex(ValueError, "event ID"):
            Notification(
                subject='Test',
                job='sync',
                status='good',
                message='ok',
                event_id='short',
            )
        with self.assertRaisesRegex(ValueError, "occurrence time"):
            Notification(
                subject='Test',
                job='sync',
                status='good',
                message='ok',
                occurred_at='2026-09-03T10:00:00',
            )

    def test_to_dict_includes_details(self):
        n = Notification(subject='Test', job='sync', status='error', message='fail', details='traceback')
        d = n.to_dict()
        self.assertEqual(d['operator']['details'], 'traceback')

    def test_to_dict_includes_hostname(self):
        n = Notification(subject='Test', job='sync', status='good', message='ok')
        d = n.to_dict()
        self.assertIn('system', d['operator'])
        self.assertIsInstance(d['operator']['system'], str)
        self.assertTrue(d['operator']['system'])  # system should be non-empty

    def test_hostname_default_is_gethostname(self):
        n = Notification(subject='Test', job='sync', status='good', message='ok')
        self.assertEqual(n.hostname, socket.gethostname())

    def test_to_dict_includes_structured_data(self):
        n = Notification(
            subject='Test',
            job='security_monitor',
            status='warning',
            message='event',
            data={'schema_version': 1, 'events': [{'type': 'fail2ban'}]},
        )

        self.assertEqual(n.to_dict()['data']['schema_version'], 1)
        self.assertEqual(n.to_dict()['data']['events'][0]['type'], 'fail2ban')

    def test_to_dict_includes_common_event_envelope(self):
        n = Notification(
            subject='Source recovered',
            job='security_monitor',
            status='info',
            message='Auditd is readable again',
            event_type='security.source_health',
            state='resolved',
            dedup_key='security_monitor:source-health',
            actions=['No action is required'],
        )

        payload = n.to_dict()

        self.assertEqual(payload['schema_version'], 2)
        self.assertEqual(payload['event']['type'], 'security.source_health')
        self.assertEqual(payload['event']['state'], 'resolved')
        self.assertEqual(payload['event']['status'], 'info')
        self.assertEqual(
            payload['event']['deduplication_key'],
            'security_monitor:source-health',
        )
        self.assertEqual(
            payload['operator']['suggested_actions'],
            ['No action is required'],
        )
        self.assertNotIn('delivery_policy', payload)

    def test_event_identity_is_stable_across_serialization(self):
        notification = Notification(
            subject='Test', job='sync', status='good', message='ok'
        )

        first = notification.to_dict()['event']
        second = notification.to_dict()['event']

        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['occurred_at'], second['occurred_at'])

    def test_hostname_can_be_overridden(self):
        n = Notification(subject='Test', job='sync', status='good', message='ok', hostname='custom-host')
        self.assertEqual(n.hostname, 'custom-host')
        self.assertEqual(n.to_dict()['operator']['system'], 'custom-host')


class TestNotificationSender(unittest.TestCase):
    def test_empty_configs_returns_true(self):
        sender = NotificationSender([])
        notification = Notification(subject='Test', job='sync', status='good', message='ok')
        self.assertTrue(sender.send(notification))

    def test_signal_policy_suppresses_routine_success(self):
        sender = NotificationSender([NotificationConfig(type='webhook', target='https://example.com/hook')])
        notification = Notification(
            subject='Sync completed',
            job='sync',
            status='good',
            message='No files changed',
            delivery_policy='signal',
        )

        with patch.object(sender, '_send_webhook') as mock_send:
            self.assertTrue(sender.send(notification))

        mock_send.assert_not_called()

    def test_signal_policy_delivers_warning(self):
        sender = NotificationSender([NotificationConfig(type='webhook', target='https://example.com/hook')])
        notification = Notification(
            subject='Sync failed',
            job='sync',
            status='error',
            message='The source was unavailable',
            delivery_policy='signal',
        )

        with patch.object(sender, '_send_webhook') as mock_send:
            self.assertTrue(sender.send(notification))

        mock_send.assert_called_once()

    @patch('subprocess.run')
    def test_mailbox_body_includes_hostname(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b'', stderr=b'')
        sender = NotificationSender([NotificationConfig(type='mailbox', target='admin@example.com')])
        notification = Notification(
            subject='Test', job='sync', status='error', message='Something failed', hostname='myserver'
        )
        sender.send(notification)
        _, kwargs = mock_run.call_args
        body = kwargs['input'].decode('utf-8')
        self.assertIn('myserver', body)

    @patch('subprocess.run')
    def test_mailbox_body_includes_details(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b'', stderr=b'')
        sender = NotificationSender([NotificationConfig(type='mailbox', target='admin@example.com')])
        notification = Notification(
            subject='Test', job='scrub', status='error',
            message='Unrepairable corruption',
            details='Unrepairable files:\n  - movies/film.mkv',
        )
        sender.send(notification)
        _, kwargs = mock_run.call_args
        body = kwargs['input'].decode('utf-8')
        self.assertIn('Details:', body)
        self.assertIn('movies/film.mkv', body)

    @patch('subprocess.run')
    def test_mailbox_body_includes_structured_data(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b'', stderr=b'')
        sender = NotificationSender([NotificationConfig(type='mailbox', target='admin@example.com')])
        notification = Notification(
            subject='Test',
            job='security_monitor',
            status='warning',
            message='Security event',
            data={'events': [{'type': 'ssh_authentication', 'failure_count': 7}]},
        )

        sender.send(notification)
        body = mock_run.call_args.kwargs['input'].decode('utf-8')
        self.assertIn('Machine-readable event data (JSON):', body)
        self.assertIn('ssh_authentication', body)

    @patch('subprocess.run')
    def test_mailbox_body_omits_details_section_when_absent(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=b'', stderr=b'')
        sender = NotificationSender([NotificationConfig(type='mailbox', target='admin@example.com')])
        notification = Notification(subject='Test', job='sync', status='good', message='ok')
        sender.send(notification)
        _, kwargs = mock_run.call_args
        body = kwargs['input'].decode('utf-8')
        self.assertNotIn('Details:', body)

    def test_failure_logs_redacted_target(self):
        log_stream = io.StringIO()
        logger = logging.getLogger('test.notifications.sender.failure')
        logger.handlers = []
        logger.propagate = False
        handler = logging.StreamHandler(log_stream)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        sender = NotificationSender(
            [NotificationConfig(type='mailbox', target='admin@example.com')],
            logger=logger,
        )
        notification = Notification(subject='Test', job='sync', status='error', message='Something failed')

        with patch.object(sender, '_send_mailbox', side_effect=Exception('boom')):
            self.assertFalse(sender.send(notification))

        output = log_stream.getvalue()
        self.assertIn('Notification delivery failed', output)
        self.assertIn("job='sync'", output)
        self.assertIn("notification_type='mailbox'", output)
        self.assertIn("target='*@example.com'", output)
        self.assertNotIn('admin@example.com', output)

    @patch('lib.notifications._open_webhook_request')
    def test_webhook_success_logs_redacted_target(self, mock_open):
        log_stream = io.StringIO()
        logger = logging.getLogger('test.notifications.sender.webhook')
        logger.handlers = []
        logger.propagate = False
        handler = logging.StreamHandler(log_stream)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 204
        response.__exit__.return_value = False
        mock_open.return_value = response

        sender = NotificationSender(
            [NotificationConfig(type='webhook', target='https://hooks.example.com/path?token=secret')],
            logger=logger,
        )
        notification = Notification(subject='Test', job='sync', status='good', message='ok')

        self.assertTrue(sender.send(notification))

        output = log_stream.getvalue()
        self.assertIn('Webhook notification sent', output)
        self.assertIn("target='https://hooks.example.com'", output)
        self.assertNotIn('token=secret', output)
        self.assertNotIn('/path', output)

        request = mock_open.call_args.args[0]
        payload = json.loads(request.data.decode('utf-8'))
        self.assertEqual(payload['schema_version'], 2)
        self.assertEqual(payload['operator']['subject'], 'Test')
        self.assertEqual(payload['operator']['what_happened'], 'ok')
        self.assertEqual(payload['operator']['suggested_actions'], [])
        self.assertEqual(payload['operator']['details'], '')
        self.assertEqual(
            request.get_header('X-infra-tools-event-id'),
            payload['event']['id'],
        )

    @patch('lib.notifications._open_webhook_request')
    def test_webhook_fragment_is_sent_as_bearer_token_not_in_url(self, mock_open):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 202
        response.__exit__.return_value = False
        mock_open.return_value = response
        token = "a" * 43
        sender = NotificationSender(
            [
                NotificationConfig(
                    type='webhook',
                    target=f'https://panel.example/api/v1/notifications#{token}',
                )
            ]
        )

        self.assertTrue(
            sender.send(
                Notification(
                    subject='Test',
                    job='sync',
                    status='good',
                    message='ok',
                )
            )
        )

        request = mock_open.call_args.args[0]
        self.assertEqual(
            request.full_url,
            'https://panel.example/api/v1/notifications',
        )
        self.assertEqual(request.get_header('Authorization'), f'Bearer {token}')
        self.assertNotIn(token, request.full_url)

    @patch('lib.notifications.time.sleep')
    @patch('lib.notifications._open_webhook_request')
    def test_webhook_retries_transient_failures_with_the_same_event_id(
        self, mock_open, mock_sleep
    ):
        headers = Message()
        headers['Retry-After'] = '2'
        unavailable = HTTPError(
            'https://hooks.example.com/secret',
            503,
            'Unavailable',
            headers,
            None,
        )
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 205
        response.__exit__.return_value = False
        mock_open.side_effect = [unavailable, response]
        notification = Notification(
            subject='Test', job='sync', status='warning', message='retry me'
        )
        sender = NotificationSender(
            [NotificationConfig(type='webhook', target='https://hooks.example.com/secret')]
        )

        self.assertTrue(sender.send(notification))

        self.assertEqual(mock_open.call_count, 2)
        mock_sleep.assert_called_once_with(2.0)
        event_ids = [
            call.args[0].get_header('X-infra-tools-event-id')
            for call in mock_open.call_args_list
        ]
        self.assertEqual(event_ids, [notification.event_id, notification.event_id])

    @patch('lib.notifications.time.sleep')
    @patch('lib.notifications._open_webhook_request')
    def test_webhook_does_not_retry_permanent_http_failure(
        self, mock_open, mock_sleep
    ):
        mock_open.side_effect = HTTPError(
            'https://hooks.example.com/secret',
            401,
            'Unauthorized',
            Message(),
            None,
        )
        sender = NotificationSender(
            [NotificationConfig(type='webhook', target='https://hooks.example.com/secret')]
        )

        self.assertFalse(
            sender.send(
                Notification(
                    subject='Test', job='sync', status='error', message='failed'
                )
            )
        )

        mock_open.assert_called_once()
        mock_sleep.assert_not_called()

    @patch('lib.notifications.time.sleep')
    @patch('lib.notifications._open_webhook_request')
    def test_webhook_does_not_retry_tls_failure(self, mock_open, mock_sleep):
        mock_open.side_effect = URLError(ssl.SSLError("TLS negotiation failed"))
        sender = NotificationSender(
            [NotificationConfig(type='webhook', target='https://hooks.example.com/secret')]
        )

        self.assertFalse(
            sender.send(
                Notification(
                    subject='Test', job='sync', status='error', message='failed'
                )
            )
        )

        mock_open.assert_called_once()
        mock_sleep.assert_not_called()

    def test_redirect_handler_refuses_to_construct_a_followup_request(self):
        handler = _RejectWebhookRedirects()

        redirected = handler.redirect_request(
            urllib.request.Request(
                'https://hooks.example.com/private',
                data=b'{}',
                headers={'Authorization': 'Bearer secret'},
                method='POST',
            ),
            None,
            302,
            'Found',
            Message(),
            'https://attacker.example/collect',
        )

        self.assertIsNone(redirected)

    @patch('urllib.request.build_opener')
    def test_webhook_requests_install_the_no_redirect_handler(self, mock_build):
        response = unittest.mock.MagicMock()
        mock_build.return_value.open.return_value = response
        request = urllib.request.Request(
            'https://hooks.example.com/private',
            data=b'{}',
            method='POST',
        )

        self.assertIs(_open_webhook_request(request), response)

        handler = mock_build.call_args.args[0]
        self.assertIsInstance(handler, _RejectWebhookRedirects)
        mock_build.return_value.open.assert_called_once_with(
            request,
            timeout=30,
        )


class TestParseNotificationArgs(unittest.TestCase):
    def test_none_args(self):
        self.assertEqual(parse_notification_args(None), [])

    def test_empty_list(self):
        self.assertEqual(parse_notification_args([]), [])

    def test_valid_webhook(self):
        configs = parse_notification_args([['webhook', 'https://example.com/hook']])
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].type, 'webhook')

    def test_valid_mailbox(self):
        configs = parse_notification_args([['mailbox', 'admin@example.com']])
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].type, 'mailbox')

    def test_multiple(self):
        configs = parse_notification_args([
            ['webhook', 'https://hook1.com'],
            ['mailbox', 'user@example.com'],
        ])
        self.assertEqual(len(configs), 2)

    def test_duplicate_targets_are_normalized_and_sent_once(self):
        configs = parse_notification_args(
            [
                ['webhook', ' https://example.com/hook '],
                ['webhook', 'https://example.com/hook'],
            ]
        )

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].target, 'https://example.com/hook')

    def test_invalid_type_skipped(self):
        configs = parse_notification_args([['sms', '+1234']])
        self.assertEqual(len(configs), 0)

    def test_wrong_arg_count_skipped(self):
        configs = parse_notification_args([['webhook']])
        self.assertEqual(len(configs), 0)


class TestValidateNotificationArgs(unittest.TestCase):
    def test_valid_webhook_notification(self):
        validate_notification_args([['webhook', 'https://example.com/hook']])

    def test_valid_mailbox_notification(self):
        validate_notification_args([['mailbox', 'admin@example.com']])

    def test_invalid_webhook_url_raises(self):
        with self.assertRaisesRegex(ValueError, "Invalid webhook URL"):
            validate_notification_args([['webhook', 'ftp://example.com/hook']])

    def test_invalid_webhook_error_does_not_reflect_credentials(self):
        target = "https://operator:supersecret@hooks.example.com/ingest"

        with self.assertRaises(ValueError) as raised:
            validate_notification_args([["webhook", target]])

        self.assertEqual(str(raised.exception), "Invalid webhook URL")
        self.assertNotIn("supersecret", str(raised.exception))

    def test_invalid_mailbox_raises(self):
        with self.assertRaisesRegex(ValueError, "Invalid mailbox address"):
            validate_notification_args([['mailbox', 'not-an-email']])

    def test_wrong_arg_count_raises(self):
        with self.assertRaisesRegex(ValueError, "--notify requires TYPE and TARGET"):
            validate_notification_args([['webhook']])

    def test_bearer_fragment_requires_https_and_urlsafe_token(self):
        token = "a" * 43
        validate_notification_args(
            [['webhook', f'https://panel.example/api/v1/notifications#{token}']]
        )
        with self.assertRaisesRegex(ValueError, "Invalid webhook URL"):
            validate_notification_args(
                [['webhook', f'http://panel.example/api/v1/notifications#{token}']]
            )
        with self.assertRaisesRegex(ValueError, "Invalid webhook URL"):
            validate_notification_args(
                [['webhook', 'https://panel.example/api/v1/notifications#short']]
            )

    def test_mailbox_target_cannot_be_interpreted_as_a_command_option(self):
        with self.assertRaisesRegex(ValueError, "Invalid mailbox address"):
            validate_notification_args([['mailbox', '-f@example.com']])

    def test_webhook_target_rejects_control_characters(self):
        with self.assertRaisesRegex(ValueError, "Invalid webhook URL"):
            validate_notification_args(
                [['webhook', 'https://example.com/hook\nInjected: value']]
            )


class TestSendSetupNotification(unittest.TestCase):
    @patch('lib.notifications.NotificationSender.send')
    def test_success_notification(self, mock_send):
        mock_send.return_value = True
        result = send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_web',
            host='10.0.0.1',
            success=True,
        )
        self.assertTrue(result)
        mock_send.assert_called_once()
        notification = mock_send.call_args[0][0]
        self.assertEqual(notification.job, 'setup')
        self.assertEqual(notification.status, 'good')
        self.assertIn('server_web', notification.subject)
        self.assertIn('10.0.0.1', notification.subject)
        self.assertIn('successfully', notification.message)

    @patch('lib.notifications.NotificationSender.send')
    def test_failure_notification(self, mock_send):
        mock_send.return_value = True
        result = send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_web',
            host='10.0.0.1',
            success=False,
            errors=["Step 'install_nginx' failed: command error"],
        )
        self.assertTrue(result)
        notification = mock_send.call_args[0][0]
        self.assertEqual(notification.status, 'error')
        self.assertIn('failed', notification.subject)
        self.assertIn('install_nginx', notification.details)

    @patch('lib.notifications.NotificationSender.send')
    def test_failure_with_multiple_errors(self, mock_send):
        mock_send.return_value = True
        errors = ["Error 1", "Error 2"]
        send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_web',
            host='10.0.0.1',
            success=False,
            errors=errors,
        )
        notification = mock_send.call_args[0][0]
        self.assertIn('Errors (2)', notification.details)
        self.assertIn('Error 1', notification.details)
        self.assertIn('Error 2', notification.details)

    def test_no_notify_specs_returns_true(self):
        result = send_setup_notification(
            notify_specs=None,
            system_type='server_web',
            host='10.0.0.1',
            success=True,
        )
        self.assertTrue(result)

    def test_empty_notify_specs_returns_true(self):
        result = send_setup_notification(
            notify_specs=[],
            system_type='server_web',
            host='10.0.0.1',
            success=True,
        )
        self.assertTrue(result)

    @patch('lib.notifications.NotificationSender.send')
    def test_success_no_errors_in_details(self, mock_send):
        mock_send.return_value = True
        send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_lite',
            host='localhost',
            success=True,
        )
        notification = mock_send.call_args[0][0]
        self.assertNotIn('Errors', notification.details)
        self.assertIn('server_lite', notification.details)
        self.assertIn('localhost', notification.details)

    @patch('lib.notifications.NotificationSender.send')
    def test_friendly_name_in_success_subject(self, mock_send):
        mock_send.return_value = True
        send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_lite',
            host='192.168.0.33',
            success=True,
            friendly_name='scrapbox',
        )
        notification = mock_send.call_args[0][0]
        self.assertIn('scrapbox', notification.subject)
        self.assertIn('192.168.0.33', notification.subject)
        self.assertIn('scrapbox', notification.message)

    @patch('lib.notifications.NotificationSender.send')
    def test_friendly_name_in_failure_subject(self, mock_send):
        mock_send.return_value = True
        send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_lite',
            host='192.168.0.33',
            success=False,
            errors=['Step failed'],
            friendly_name='scrapbox',
        )
        notification = mock_send.call_args[0][0]
        self.assertIn('scrapbox', notification.subject)
        self.assertIn('failed', notification.subject)

    @patch('lib.notifications.NotificationSender.send')
    def test_friendly_name_in_details(self, mock_send):
        mock_send.return_value = True
        send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_lite',
            host='localhost',
            success=True,
            friendly_name='mybox',
        )
        notification = mock_send.call_args[0][0]
        self.assertIn('Name: mybox', notification.details)

    @patch('lib.notifications.NotificationSender.send')
    def test_no_friendly_name_no_name_in_details(self, mock_send):
        mock_send.return_value = True
        send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_lite',
            host='localhost',
            success=True,
        )
        notification = mock_send.call_args[0][0]
        self.assertNotIn('Name:', notification.details)

    @patch('lib.notifications.NotificationSender.send')
    def test_no_friendly_name_host_only_in_subject(self, mock_send):
        mock_send.return_value = True
        send_setup_notification(
            notify_specs=[['webhook', 'https://example.com/hook']],
            system_type='server_lite',
            host='10.0.0.1',
            success=True,
        )
        notification = mock_send.call_args[0][0]
        self.assertIn('10.0.0.1', notification.subject)
        # Should not have parentheses around host when no friendly_name
        self.assertNotIn('(', notification.subject)


class TestSendNotificationSafe(unittest.TestCase):
    @patch('lib.notifications.send_notification')
    def test_returns_without_configs(self, mock_send):
        self.assertTrue(send_notification_safe([], "Subject", "job", "info", "msg"))
        mock_send.assert_not_called()

    @patch('lib.notifications.send_notification', side_effect=RuntimeError('boom'))
    def test_suppresses_errors(self, _mock_send):
        send_notification_safe(
            [NotificationConfig(type='webhook', target='https://example.com/hook')],
            "Subject",
            "job",
            "error",
            "msg"
        )

    @patch('lib.notifications.send_notification', side_effect=RuntimeError('boom'))
    def test_logs_structured_warning_when_logger_present(self, _mock_send):
        log_stream = io.StringIO()
        logger = logging.getLogger('test.notifications.safe')
        logger.handlers = []
        logger.propagate = False
        handler = logging.StreamHandler(log_stream)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        send_notification_safe(
            [NotificationConfig(type='webhook', target='https://example.com/hook')],
            "Subject",
            "job",
            "error",
            "msg",
            logger=logger,
        )

        output = log_stream.getvalue()
        self.assertIn('Notification send suppressed after delivery failure', output)
        self.assertIn("job='job'", output)
        self.assertIn("error='boom'", output)

    @patch('lib.notifications.send_notification', return_value=False)
    def test_logs_incomplete_delivery_when_sender_reports_failure(self, _mock_send):
        log_stream = io.StringIO()
        logger = logging.getLogger('test.notifications.incomplete')
        logger.handlers = []
        logger.propagate = False
        handler = logging.StreamHandler(log_stream)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        send_notification_safe(
            [NotificationConfig(type='webhook', target='https://example.com/hook')],
            'Subject',
            'job',
            'warning',
            'msg',
            logger=logger,
        )

        self.assertIn('Notification delivery incomplete', log_stream.getvalue())

    @patch('lib.notifications.send_notification', return_value=True)
    def test_returns_delivery_result(self, _mock_send):
        self.assertTrue(
            send_notification_safe(
                [NotificationConfig(type='webhook', target='https://example.com/hook')],
                'Subject',
                'job',
                'info',
                'msg',
            )
        )


class TestLoadNotificationConfigsFromState(unittest.TestCase):
    @patch(
        'lib.machine_state.load_notification_state',
        return_value={
            'notify_specs': [['mailbox', 'ops@example.com']],
            'notification_level': 'warning',
        },
    )
    def test_loads_readable_notification_state(self, _mock_notification):
        configs = load_notification_configs_from_state()

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].target, 'ops@example.com')
        self.assertEqual(configs[0].level, 'warning')

    @patch('lib.machine_state.load_notification_state', return_value=None)
    @patch('lib.machine_state.load_setup_config', side_effect=AssertionError)
    def test_missing_notification_state_does_not_read_full_setup_state(
        self, _mock_setup, _mock_notification
    ):
        self.assertEqual(load_notification_configs_from_state(), [])

    @patch('lib.machine_state.load_notification_state', side_effect=ValueError('bad state'))
    def test_logs_structured_warning_on_load_failure(self, _mock_load):
        log_stream = io.StringIO()
        logger = logging.getLogger('test.notifications.state')
        logger.handlers = []
        logger.propagate = False
        handler = logging.StreamHandler(log_stream)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        self.assertEqual(load_notification_configs_from_state(logger=logger), [])

        output = log_stream.getvalue()
        self.assertIn('Failed to load notification configs from machine state', output)
        self.assertIn("error='bad state'", output)

    @patch(
        'lib.machine_state.load_notification_state',
        return_value={'notify_specs': [['webhook', 'ftp://invalid.example/hook']]},
    )
    def test_rejects_corrupt_saved_notification_targets(self, _mock_load):
        log_stream = io.StringIO()
        logger = logging.getLogger('test.notifications.state.invalid-target')
        logger.handlers = []
        logger.propagate = False
        logger.addHandler(logging.StreamHandler(log_stream))
        logger.setLevel(logging.INFO)

        self.assertEqual(load_notification_configs_from_state(logger=logger), [])
        self.assertIn(
            'Failed to load notification configs from machine state',
            log_stream.getvalue(),
        )

    @patch(
        'lib.machine_state.load_notification_state',
        return_value={
            'notify_specs': [
                ['webhook', 'ftp://invalid.example/hook'],
                ['mailbox', 'ops@example.com'],
            ]
        },
    )
    def test_keeps_valid_saved_targets_when_another_target_is_corrupt(
        self, _mock_load
    ):
        log_stream = io.StringIO()
        logger = logging.getLogger('test.notifications.state.partial-targets')
        logger.handlers = []
        logger.propagate = False
        logger.addHandler(logging.StreamHandler(log_stream))
        logger.setLevel(logging.INFO)

        configs = load_notification_configs_from_state(logger=logger)

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].target, 'ops@example.com')
        self.assertIn('Ignored 1 invalid target(s)', log_stream.getvalue())


if __name__ == '__main__':
    unittest.main()
