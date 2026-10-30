"""Check the root CLI documentation surface without executing parser sources."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from scripts import check_cli_docs


class CliDocumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, path: str, source: str) -> None:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(textwrap.dedent(source), encoding="utf-8")

    def parser(self, body: str, imports: str = "") -> None:
        self.write("basaltwater.py", imports + "\n"
                   "def create_basaltwater_parser():\n"
                   "    parser = object()\n"
                   "    subparsers = parser.add_subparsers()\n"
                   + textwrap.indent(textwrap.dedent(body), "    "))

    def test_imported_and_local_builders_aliases_and_nested_commands(self) -> None:
        self.parser('''\
            subparsers.add_parser("direct", aliases=["short"])
            add_tools(subparsers)
            ''', "from lib.tools import add_tools_subparser as add_tools\n"
            "raise AssertionError('parser source must not execute')")
        self.write("lib/tools.py", '''\
            import pwd
            raise AssertionError('builder source must not execute')
            def add_tools_subparser(root):
                _add_one(root)
                command = root.add_parser(name="tools", aliases=("tool",))
                nested = command.add_subparsers()
                for name in ["nested", "child"]:
                    nested.add_parser(name)
            def _add_one(collection):
                collection.add_parser("other")
            def unrelated(collection):
                collection.add_parser("unreachable")
            ''')
        with patch("subprocess.run", side_effect=AssertionError("No host operations")):
            self.assertEqual(check_cli_docs.root_commands(self.root),
                             {"direct", "short", "tools", "tool", "other"})

    def test_missing_commands_and_nonexempt_aliases_are_reported(self) -> None:
        self.parser('''\
            subparsers.add_parser("list", aliases=["ls", "inventory"])
            subparsers.add_parser("added")
            ''')
        self.write("docs/reference/command-line.md", "basaltw list\nbasaltw parent added\n")
        output = StringIO()
        with patch.object(check_cli_docs, "ROOT", self.root), patch.object(
            check_cli_docs, "DOC", self.root / "docs/reference/command-line.md"
        ), redirect_stdout(output):
            self.assertEqual(check_cli_docs.main(), 1)
            self.assertIn("added, inventory", output.getvalue())
            self.write("docs/reference/command-line.md",
                       "basaltw list\nbasaltw inventory\nbasaltw added\n")
            self.assertEqual(check_cli_docs.main(), 0)

    def test_unsupported_registration_fails_with_location_and_guidance(self) -> None:
        cases = [
            'subparsers.add_parser(command)',
            'subparsers.add_parser("one", aliases=ALIASES)',
            'subparsers.add_parser("one", **options)',
            'for name in ["one"]:\n    subparsers.add_parser(name)',
            'if enabled:\n    subparsers.add_parser("one")',
            'alias = subparsers\nalias.add_parser("one")',
            'register(subparsers)',
            'subparsers = other',
            'getattr(subparsers, "add_parser")("one")',
        ]
        for body in cases:
            with self.subTest(body=body):
                self.parser(body)
                with self.assertRaisesRegex(check_cli_docs.RegistrationError,
                                            r"basaltwater.py:\d+: .*extend scripts/check_cli_docs.py"):
                    check_cli_docs.root_commands(self.root)

    def test_unsupported_imported_builder_is_not_silently_omitted(self) -> None:
        self.parser('subparsers.add_parser("known")\nadd_more(subparsers)',
                    "from lib.tools import add_more")
        self.write("lib/tools.py", '''\
            def add_more(root):
                if enabled:
                    root.add_parser("hidden")
            ''')
        output = StringIO()
        with patch.object(check_cli_docs, "ROOT", self.root), redirect_stderr(output):
            self.assertEqual(check_cli_docs.main(), 1)
        self.assertIn("tools.py:3:", output.getvalue())
        self.assertIn("dynamic root registration", output.getvalue())

    def test_recursive_builder_fails(self) -> None:
        self.parser('add_more(subparsers)', "from lib.tools import add_more")
        self.write("lib/tools.py", "def add_more(root):\n    add_more(root)\n")
        with self.assertRaisesRegex(check_cli_docs.RegistrationError, "recursive parser builder"):
            check_cli_docs.root_commands(self.root)

    def test_missing_or_empty_parser_cannot_pass(self) -> None:
        for source in (None, "pass", '"""No registrations yet."""'):
            with self.subTest(source=source):
                if source is not None:
                    self.parser(source)
                output = StringIO()
                with patch.object(check_cli_docs, "ROOT", self.root), redirect_stderr(output):
                    self.assertEqual(check_cli_docs.main(), 1)
                self.assertIn("CLI documentation check failed:", output.getvalue())

    @unittest.skipIf(os.name == "nt", "Runtime controller parser requires POSIX")
    def test_repository_registrations_match_runtime_parser(self) -> None:
        import basaltwater

        parser, _, _ = basaltwater.create_basaltwater_parser()
        self.assertEqual(check_cli_docs.root_commands(check_cli_docs.ROOT),
                         set(parser._subparsers._group_actions[0].choices))


if __name__ == "__main__":
    unittest.main()
