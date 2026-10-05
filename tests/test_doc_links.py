"""Exercise documentation links without fetching upstream URLs or running examples."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_doc_links import REPOSITORY_DOC_URL, check_links


class DocumentationLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, path: str, text: str) -> None:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")

    def test_valid_guides_skill_resources_and_heading_variants(self) -> None:
        self.write("docs/guide.md", "# Guide\n## Install `tool` & sign in\n## Repeat\n## Repeat\n<a id='manual-anchor'></a>\n")
        self.write("docs/file name.md", "# Encoded path\n")
        self.write("common/agent_skills/task/references/workflow.md", "# Workflow\n")
        self.write("common/agent_skills/task/SKILL.md", "[resource](references/workflow.md#workflow)\n")
        self.write("README.md", "\n".join([
            "[install](docs/guide.md#install-tool--sign-in)",
            "[duplicate](docs/guide.md#repeat-1)",
            "[manual](docs/guide.md#manual-anchor)",
            "[encoded](docs/file%20name.md#encoded-path)",
            "[angle](<docs/file name.md> \"Guide\")",
            "[directory](docs/)",
            f"[packaged guide]({REPOSITORY_DOC_URL}docs/guide.md#guide)",
            "[reference][guide]",
            "[guide]: docs/guide.md#guide",
        ]))
        with patch("subprocess.run", side_effect=AssertionError("No examples should execute")):
            self.assertEqual(check_links(self.root), [])

    def test_broken_file_anchor_and_reference_have_original_line_numbers(self) -> None:
        self.write("docs/guide.md", "# Install and sign in\n")
        self.write("README.md", "```bash\n[example](ignored.md)\n```\n"
                   "[missing](docs/missing.md)\n[old](docs/guide.md#install-the-panel)\n"
                   "[reference]: docs/gone.md\n")
        errors = check_links(self.root)
        self.assertEqual(len(errors), 3)
        self.assertIn("README.md:4: missing local path", errors[0])
        self.assertIn("README.md:5: missing heading or anchor", errors[1])
        self.assertIn("README.md:6: missing local path", errors[2])

    def test_examples_comments_and_external_artifacts_are_not_local_links(self) -> None:
        self.write("README.md", "\n".join([
            "~~~markdown", "[example](missing.md)", "~~~",
            "```markdown", "## Not an anchor", "[example](missing.md)", "```",
            "`![example](/absolute/path.png)`",
            "<!-- [old](missing.md) -->",
            "[upstream](https://example.org/guide.md#anything)",
            "[artifact](/absolute/capture.png)",
        ]))
        self.assertEqual(check_links(self.root), [])
        self.write("docs/guide.md", "[invalid](../README.md#not-an-anchor)\n")
        self.assertEqual(len(check_links(self.root)), 1)

    def test_skill_reference_and_repository_url_anchors_are_checked(self) -> None:
        self.write("docs/guide.md", "# Guide\n")
        self.write("common/agent_skills/task/SKILL.md",
                   "[missing](references/missing.md)\n"
                   f"[stale]({REPOSITORY_DOC_URL}docs/guide.md#old)\n")
        errors = check_links(self.root)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("common/agent_skills/task/SKILL.md:" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
