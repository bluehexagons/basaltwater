#!/usr/bin/env python3
"""
Manage CI/CD Webhook Configuration

Helper script for managing webhook configurations and testing the CI/CD system.

Usage:
    webhook-manager.py list              - List configured repositories
    webhook-manager.py add <url>         - Add repository configuration
    webhook-manager.py remove <url>      - Remove repository configuration
    webhook-manager.py show-secret       - Display webhook secret
    webhook-manager.py test <url>        - Test webhook configuration
    webhook-manager.py logs [service]    - Show service logs
    webhook-manager.py status            - Show service status
"""

from __future__ import annotations

import sys
import os
import json
import subprocess
import argparse

from lib.local_http import open_loopback
from web.service_tools.cicd_config import load_config_file, save_config_file
from web.service_tools.cicd_security import DEFAULT_BRANCHES

try:
    import argcomplete
except ImportError:
    argcomplete = None

CONFIG_FILE = "/etc/basaltwater/cicd/webhook_config.json"
SECRET_FILE = "/etc/basaltwater/cicd/webhook_secret"


def load_config() -> dict:
    """Load webhook configuration."""
    if not os.path.exists(CONFIG_FILE):
        return {"repositories": []}
    
    return load_config_file(CONFIG_FILE)


def save_config(config: dict) -> None:
    """Save webhook configuration."""
    save_config_file(CONFIG_FILE, config)
    
    print(f"Configuration saved to {CONFIG_FILE}")


def list_repositories(args: argparse.Namespace) -> int:
    """List configured repositories."""
    config = load_config()
    repos = config.get('repositories', [])
    
    if not repos:
        print("No repositories configured")
        return 0
    
    print(f"Configured repositories ({len(repos)}):\n")
    
    for i, repo in enumerate(repos, 1):
        print(f"{i}. {repo['url']}")
        print(f"   Branches: {', '.join(repo.get('branches', ['main']))}")
        scripts = repo.get('scripts', {})
        if scripts:
            print(f"   Scripts: {', '.join(scripts.keys())}")
        print()
    
    return 0


def add_repository(args: argparse.Namespace) -> int:
    """Add repository configuration."""
    config = load_config()
    repos = config.get('repositories', [])
    
    # Check if repository already exists
    for repo in repos:
        if repo['url'] == args.url:
            print(f"Repository already configured: {args.url}")
            return 1
    
    # Create new repository configuration
    new_repo = {
        "url": args.url,
        "branches": args.branches or list(DEFAULT_BRANCHES),
        "scripts": {}
    }
    
    # Add script paths if provided
    if args.install:
        new_repo["scripts"]["install"] = args.install
    if args.build:
        new_repo["scripts"]["build"] = args.build
    if args.test:
        new_repo["scripts"]["test"] = args.test
    if args.deploy:
        new_repo["scripts"]["deploy"] = args.deploy
    
    repos.append(new_repo)
    config['repositories'] = repos
    save_config(config)
    
    print(f"Added repository: {args.url}")
    return 0


def remove_repository(args: argparse.Namespace) -> int:
    """Remove repository configuration."""
    config = load_config()
    repos = config.get('repositories', [])
    
    # Find and remove repository
    new_repos = [repo for repo in repos if repo['url'] != args.url]
    
    if len(new_repos) == len(repos):
        print(f"Repository not found: {args.url}")
        return 1
    
    config['repositories'] = new_repos
    save_config(config)
    
    print(f"Removed repository: {args.url}")
    return 0


def show_secret(args: argparse.Namespace) -> int:
    """Display webhook secret."""
    if not os.path.exists(SECRET_FILE):
        print(f"Secret file not found: {SECRET_FILE}")
        return 1
    
    with open(SECRET_FILE, 'r') as f:
        secret = f.read().strip()
    
    print("Webhook Secret:")
    print(secret)
    print("\nUse this secret when configuring GitHub webhooks.")
    return 0


def test_configuration(args: argparse.Namespace) -> int:
    """Test webhook configuration for a repository."""
    config = load_config()
    repos = config.get('repositories', [])
    
    # Find repository
    repo = None
    for r in repos:
        if r['url'] == args.url:
            repo = r
            break
    
    if not repo:
        print(f"Repository not configured: {args.url}")
        return 1
    
    print(f"Configuration for {args.url}:\n")
    print(json.dumps(repo, indent=2))
    
    # Check if scripts exist
    print("\nScript validation:")
    scripts = repo.get('scripts', {})
    for script_type, script_path in scripts.items():
        if os.path.isabs(script_path):
            exists = os.path.exists(script_path)
        else:
            # For relative paths, we can't validate without knowing the workspace
            exists = None
        
        if exists is None:
            status = "⚠️  (relative path, will be resolved at runtime)"
        elif exists:
            status = "✓"
        else:
            status = "✗ (not found)"
        
        print(f"  {script_type}: {script_path} {status}")
    
    return 0


def show_logs(args: argparse.Namespace) -> int:
    """Show service logs."""
    service = args.service or "webhook-receiver"
    
    if service not in ["webhook-receiver", "cicd-executor"]:
        print(f"Unknown service: {service}")
        print("Available services: webhook-receiver, cicd-executor")
        return 1
    
    try:
        # Show last 50 lines by default, follow if -f flag is present
        cmd = ["journalctl", "-u", f"{service}.service", "-n", "50"]
        if args.follow:
            cmd.append("-f")
        
        subprocess.run(cmd)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def show_status(args: argparse.Namespace) -> int:
    """Show service status."""
    services = ["webhook-receiver", "cicd-executor"]
    
    print("CI/CD System Status:\n")
    
    for service in services:
        print(f"=== {service}.service ===")
        result = subprocess.run(
            ["systemctl", "status", f"{service}.service"],
            capture_output=True,
            text=True
        )
        
        # Extract key info from status output
        lines = result.stdout.split('\n')
        for line in lines[:5]:  # Show first 5 lines
            print(line)
        print()
    
    # Show health check
    print("=== Health Check ===")
    try:
        with open_loopback("http://127.0.0.1:8765/health", timeout=2) as response:
            if response.status == 200:
                print("✓ Webhook receiver is responding")
            else:
                print(f"⚠️  Webhook receiver returned status {response.status}")
    except Exception as e:
        print(f"✗ Webhook receiver is not responding: {e}")
    
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage CI/CD webhook configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # list command
    subparsers.add_parser('list', help='List configured repositories')
    
    # add command
    add_parser = subparsers.add_parser('add', help='Add repository configuration')
    add_parser.add_argument('url', help='Repository URL')
    add_parser.add_argument('--branches', nargs='+', help='Branches to build (default: main)')
    add_parser.add_argument('--install', help='Install script path')
    add_parser.add_argument('--build', help='Build script path')
    add_parser.add_argument('--test', help='Test script path')
    add_parser.add_argument('--deploy', help='Deploy script path')
    
    # remove command
    remove_parser = subparsers.add_parser('remove', help='Remove repository configuration')
    remove_parser.add_argument('url', help='Repository URL')
    
    # show-secret command
    subparsers.add_parser('show-secret', help='Display webhook secret')
    
    # test command
    test_parser = subparsers.add_parser('test', help='Test webhook configuration')
    test_parser.add_argument('url', help='Repository URL')
    
    # logs command
    logs_parser = subparsers.add_parser('logs', help='Show service logs')
    logs_parser.add_argument('service', nargs='?', choices=['webhook-receiver', 'cicd-executor'],
                           help='Service name (default: webhook-receiver)')
    logs_parser.add_argument('-f', '--follow', action='store_true', help='Follow log output')
    
    # status command
    subparsers.add_parser('status', help='Show service status')
    
    if argcomplete:
        argcomplete.autocomplete(parser)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Commands explicitly grouped by privilege level to follow least-privilege principle
    privileged_commands = ['add', 'remove', 'show-secret', 'logs', 'status']
    # list and test commands don't require root (list only reads config, test validates config)
    
    if args.command in privileged_commands:
        if os.geteuid() != 0:
            print("Error: This command requires root privileges")
            print("Run with: sudo webhook-manager.py ...")
            return 1
    
    commands = {
        'list': list_repositories,
        'add': add_repository,
        'remove': remove_repository,
        'show-secret': show_secret,
        'test': test_configuration,
        'logs': show_logs,
        'status': show_status,
    }
    
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
