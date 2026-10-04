"""Keep shipped skill examples and resources aligned with repository contracts."""

from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

from basaltwater import create_basaltwater_parser
from common.agent_steps import AGENT_SKILLS_ROOT
from common.service_tools.basaltwater_web import _parser as gateway_parser


class AgentSkillContractTest(unittest.TestCase):
    def test_fenced_managed_commands_parse_without_executing_operations(self) -> None:
        parsers = {"basaltw": create_basaltwater_parser()[0], "basaltwater-web": gateway_parser()}
        checked: set[str] = set()
        with patch("subprocess.run", side_effect=AssertionError("Examples must not execute")), patch(
            "subprocess.Popen", side_effect=AssertionError("Examples must not launch processes"),
        ):
            for path in sorted(Path(AGENT_SKILLS_ROOT).rglob("*.md")):
                blocks = re.findall(r"(?m)^\s*```bash\n(.*?)^\s*```", path.read_text(encoding="utf-8"), re.S)
                for block in blocks:
                    for line in block.replace("\\\n", " ").splitlines():
                        tokens = shlex.split(line.strip())
                        if not tokens or tokens[0] not in parsers:
                            continue
                        # Example observation tokens stand in for integer arguments.
                        argv = [{"PID": "1234", "PORT": "8765"}.get(token, token) for token in tokens[1:]]
                        # The gateway dispatcher separates custom preview argv itself.
                        if tokens[0] == "basaltwater-web" and "--" in argv:
                            argv = argv[:argv.index("--")]
                        with self.subTest(path=path, command=line):
                            parsers[tokens[0]].parse_args(argv)
                        checked.add(tokens[0])
        self.assertEqual(checked, set(parsers))

    def test_markdown_references_resolve_to_shipped_resources_or_repository_docs(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        repo_url = "https://github.com/bluehexagons/basaltwater/blob/main/"
        for path in sorted(Path(AGENT_SKILLS_ROOT).rglob("*.md")):
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if target.startswith(repo_url):
                    destination = repository / target[len(repo_url):].split("#", 1)[0]
                elif ":" not in target and target.split("#", 1)[0].endswith(".md"):
                    destination = path.parent / target.split("#", 1)[0]
                    skill_root = Path(AGENT_SKILLS_ROOT) / path.relative_to(AGENT_SKILLS_ROOT).parts[0]
                    self.assertTrue(destination.resolve().is_relative_to(skill_root))
                else:
                    continue  # External references and illustrative evidence paths.
                with self.subTest(path=path, target=target):
                    self.assertTrue(destination.is_file(), f"Missing skill reference: {target}")


if __name__ == "__main__":
    unittest.main()
