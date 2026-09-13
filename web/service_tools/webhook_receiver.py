#!/usr/bin/env python3
"""
Webhook Receiver for CI/CD

A secure webhook receiver that accepts GitHub webhooks and triggers CI/CD builds.
Runs as a systemd service and listens on localhost only for security.

Security features:
- HMAC signature verification (X-Hub-Signature-256)
- Localhost-only binding
- Rate limiting via nginx
- Dedicated webhook user with limited permissions

Logs to: /var/log/infra_tools/web/webhook_receiver.log
"""

from __future__ import annotations

import os
import sys
import json
import hmac
import hashlib
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from web.service_tools.cicd_config import load_config_file
from web.service_tools.cicd_deliveries import enqueue, recover_pending
from web.service_tools.cicd_security import (
    DEFAULT_BRANCHES,
    MAX_WEBHOOK_PAYLOAD_BYTES,
    validate_branch_ref,
    validate_commit_sha,
    validate_pusher,
    validate_repo_url,
)

# Initialize centralized logger
logger = get_service_logger('webhook_receiver', 'web', use_syslog=True)

# Configuration paths
CONFIG_DIR = "/etc/infra_tools/cicd"
CONFIG_FILE = os.path.join(CONFIG_DIR, "webhook_config.json")
STATE_DIR = "/var/lib/infra_tools/cicd"
JOBS_DIR = os.path.join(STATE_DIR, "jobs")
DELIVERIES_FILE = os.path.join(STATE_DIR, 'deliveries.sqlite3')

# Server configuration
DEFAULT_PORT = 8765
WEBHOOK_SECRET_ENV = "WEBHOOK_SECRET"

# Timestamp format for job creation
TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'


def verify_github_signature(secret: str, payload: bytes, signature_header: Optional[str]) -> bool:
    """Verify GitHub webhook HMAC signature."""
    if not signature_header:
        return False
    
    # GitHub sends signature as "sha256=<signature>"
    if not signature_header.startswith("sha256="):
        return False
    
    expected_signature = signature_header[7:]  # Remove "sha256=" prefix
    if re.fullmatch(r'[0-9a-fA-F]{64}', expected_signature) is None:
        return False
    
    # Compute HMAC-SHA256
    computed_signature = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed_signature, expected_signature.lower())


def load_config() -> dict:
    """Load webhook configuration from JSON file."""
    return load_config_file(CONFIG_FILE)


def trigger_cicd_job(repo_url: str, ref: str, commit_sha: str, pusher: str, *,
                     payload_digest: str | None = None, delivery_id: str | None = None) -> bool:
    """
    Trigger CI/CD job by creating a job file in the jobs directory.
    
    The cicd-executor.service is activated automatically by a systemd .path
    unit that watches the jobs directory, so this function does not need
    elevated privileges to start the executor (and indeed cannot, since the
    webhook user has no polkit rule to call systemctl on system services).
    """
    try:
        os.makedirs(JOBS_DIR, exist_ok=True)
        
        safe_repo_url = validate_repo_url(repo_url)
        safe_ref, _branch = validate_branch_ref(ref)
        safe_commit_sha = validate_commit_sha(commit_sha)
        safe_pusher = validate_pusher(pusher)

        job_data = {
            "repo_url": safe_repo_url,
            "ref": safe_ref,
            "commit_sha": safe_commit_sha,
            "pusher": safe_pusher,
            "timestamp": datetime.now().strftime(TIMESTAMP_FORMAT)
        }
        if delivery_id is not None:
            if re.fullmatch(r'[A-Za-z0-9-]{1,128}', delivery_id) is None:
                raise ValueError('Invalid GitHub delivery ID')
            job_data['delivery_id'] = delivery_id
        key = payload_digest or hashlib.sha256(json.dumps(
            [safe_repo_url, safe_ref, safe_commit_sha, safe_pusher]
        ).encode()).hexdigest()
        enqueue(DELIVERIES_FILE, JOBS_DIR, key, job_data)
        job_file = os.path.join(JOBS_DIR, f'delivery-{key}.json')
        
        log_event(
            logger,
            "Accepted CI/CD delivery",
            job_file=job_file,
            repo_url=safe_repo_url,
            commit_sha=safe_commit_sha[:8],
        )
        
        return True
        
    except Exception as e:
        log_event(logger, "Failed to create CI/CD job", level=40, error=str(e), repo_url=repo_url)
        return False


class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for GitHub webhooks."""
    
    # Suppress default logging (we use our own logger)
    def log_message(self, format, *args):
        pass
    
    def do_POST(self):
        """Handle POST requests (GitHub webhooks)."""
        if self.path != '/webhook':
            self.send_error(404, "Not Found")
            return
        
        content_length_header = self.headers.get('Content-Length')
        if content_length_header is None:
            self.send_error(411, "Content-Length Required")
            return
        try:
            content_length = int(content_length_header)
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if content_length < 0:
            self.send_error(400, "Invalid Content-Length")
            return
        if content_length > MAX_WEBHOOK_PAYLOAD_BYTES:
            self.send_error(413, "Payload Too Large")
            return

        body = self.rfile.read(content_length)
        if len(body) != content_length:
            self.send_error(400, 'Incomplete request body')
            return
        
        # Get webhook secret from environment
        secret = os.environ.get(WEBHOOK_SECRET_ENV)
        if not secret:
            log_event(logger, "Webhook secret environment variable not set", level=40, env_var=WEBHOOK_SECRET_ENV)
            self.send_error(500, "Server Configuration Error")
            return
        
        # Verify signature
        signature = self.headers.get('X-Hub-Signature-256')
        if not verify_github_signature(secret, body, signature):
            log_event(logger, "Invalid signature", level=30, client_ip=self.client_address[0])
            self.send_error(403, "Invalid Signature")
            return
        
        # Parse JSON payload
        try:
            payload = json.loads(body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
            log_event(logger, "Invalid JSON payload", level=40, error=str(e))
            self.send_error(400, "Invalid JSON")
            return

        if not isinstance(payload, dict):
            self.send_error(400, "JSON payload must be an object")
            return
        
        # Get event type
        event_type = self.headers.get('X-GitHub-Event', 'unknown')
        
        # Handle push events
        if event_type == 'push':
            repository = payload.get('repository')
            pusher_data = payload.get('pusher', {})
            if not isinstance(repository, dict) or not isinstance(pusher_data, dict):
                self.send_error(400, "Invalid Push Payload")
                return

            repo_url = repository.get('clone_url', '')
            ref = payload.get('ref', '')
            commit_sha = payload.get('after', '')
            pusher = pusher_data.get('name', 'unknown')

            if payload.get("deleted") or (
                isinstance(commit_sha, str) and commit_sha and not commit_sha.strip("0")
            ):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ignored", "reason": "branch deleted"}).encode())
                return

            try:
                repo_url = validate_repo_url(repo_url)
                ref, branch = validate_branch_ref(ref)
                commit_sha = validate_commit_sha(commit_sha)
                pusher = validate_pusher(pusher)
            except ValueError as exc:
                log_event(logger, "Invalid push payload", level=30, error=str(exc))
                self.send_error(400, "Invalid Push Payload")
                return
            
            log_event(
                logger,
                "Received push event",
                repo_url=repo_url,
                ref=ref,
                commit_sha=commit_sha[:8],
                pusher=pusher,
            )
            
            # Load configuration to check if this repo is configured
            try:
                config = load_config()
            except (OSError, ValueError):
                self.send_error(503, 'CI/CD configuration unavailable or invalid')
                return
            repos = config.get('repositories', [])
            
            # Find matching repository configuration
            repo_config = None
            for repo in repos:
                if repo.get('url') == repo_url:
                    repo_config = repo
                    break
            
            if not repo_config:
                log_event(logger, "Repository not configured", repo_url=repo_url)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ignored", "reason": "repository not configured"}).encode())
                return
            
            # Check if the branch matches configured branches
            configured_branches = repo_config.get('branches', DEFAULT_BRANCHES)
            
            if branch not in configured_branches:
                log_event(logger, "Branch not configured", branch=branch, repo_url=repo_url)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ignored", "reason": "branch not configured"}).encode())
                return
            
            # Trigger CI/CD job
            delivery_id = self.headers.get('X-GitHub-Delivery')
            if delivery_id is not None and re.fullmatch(r'[A-Za-z0-9-]{1,128}', delivery_id) is None:
                self.send_error(400, 'Invalid GitHub delivery ID')
                return
            # Only the body is authenticated by GitHub's HMAC. Changing an
            # unsigned delivery header must not bypass replay protection.
            success = trigger_cicd_job(
                repo_url, ref, commit_sha, pusher,
                payload_digest=hashlib.sha256(body).hexdigest(), delivery_id=delivery_id,
            )
            
            if success:
                log_event(logger, "CI/CD job triggered", repo_url=repo_url, commit_sha=commit_sha[:8])
                self.send_response(202)  # Accepted
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "accepted", "commit": commit_sha[:8]}).encode())
            else:
                log_event(logger, "Failed to trigger CI/CD job", level=40, repo_url=repo_url, commit_sha=commit_sha[:8])
                self.send_error(503, "CI/CD admission unavailable; retry later")
        
        elif event_type == 'ping':
            # Handle ping events (sent when webhook is first created)
            log_event(logger, "Received ping event")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "pong"}).encode())
        
        else:
            # Ignore other event types
            log_event(logger, "Ignored event type", event_type=event_type)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ignored", "reason": f"event type {event_type} not supported"}).encode())
    
    def do_GET(self):
        """Handle GET requests (health check)."""
        if self.path == '/health':
            try:
                load_config()
            except (OSError, ValueError):
                self.send_error(503, 'CI/CD configuration unavailable or invalid')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_error(404, "Not Found")


def main():
    """Main function to run the webhook receiver server."""
    log_event(logger, "Starting webhook receiver")
    
    # Get port from environment or use default
    port = int(os.environ.get('WEBHOOK_PORT', DEFAULT_PORT))
    
    # Verify webhook secret is configured
    if not os.environ.get(WEBHOOK_SECRET_ENV):
        log_event(logger, "Webhook secret environment variable not set", level=40, env_var=WEBHOOK_SECRET_ENV)
        return 1
    
    # Create jobs directory if it doesn't exist
    os.makedirs(JOBS_DIR, exist_ok=True)
    recover_pending(DELIVERIES_FILE, JOBS_DIR)
    
    # Start HTTP server (bind to localhost only for security)
    server_address = ('127.0.0.1', port)
    httpd = HTTPServer(server_address, WebhookHandler)
    
    log_event(logger, "Webhook receiver listening", bind="127.0.0.1", port=port)
    log_event(logger, "Server is ready to accept webhooks")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log_event(logger, "Shutting down webhook receiver")
        httpd.shutdown()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
