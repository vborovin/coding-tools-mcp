from __future__ import annotations

import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from coding_tools_mcp import server as server_module
from coding_tools_mcp.server import Runtime, ToolFailure, WorkspaceMutationPolicy


ENV = "CODING_TOOLS_MCP_EXEC_WRITE_ROOTS"


class ExecWriteRootsTests(unittest.TestCase):
    def test_default_and_explicit_roots(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            cache = base / "cargo"
            workspace.mkdir()
            cache.mkdir()
            with patch.dict(os.environ, {ENV: ""}):
                runtime = Runtime(workspace)
                try:
                    self.assertEqual(runtime.landlock_write_roots(), [runtime.runtime_dir])
                finally:
                    runtime.close()
            with patch.dict(os.environ, {ENV: os.pathsep.join([str(cache), str(cache)])}):
                for mode in ("safe", "trusted"):
                    runtime = Runtime(workspace, permission_mode=mode)
                    try:
                        self.assertEqual(runtime.landlock_write_roots(), [runtime.runtime_dir, cache.resolve()])
                        self.assertEqual(runtime.server_info_payload()["exec_write_roots"], [str(cache.resolve())])
                        self.assertEqual(runtime.check_exec_environment({})["exec_write_roots"], [str(cache.resolve())])
                        with self.assertRaises(ToolFailure):
                            runtime.workspace.resolve_existing(str(cache))
                        with self.assertRaises(ToolFailure):
                            runtime._check_command_path_candidate(str(cache))
                    finally:
                        runtime.close()

    def test_invalid_roots_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            workspace.mkdir()
            child = workspace / "child"
            child.mkdir()
            file = base / "file"
            file.touch()
            for root in ("relative", str(base), str(workspace), str(child),
                         str(base / "missing"), str(file), str(base.anchor)):
                with self.subTest(root=root), patch.dict(os.environ, {ENV: root}):
                    with self.assertRaises(ToolFailure) as raised:
                        Runtime(workspace)
                    self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_dangerous_mode_rejects_explicit_roots(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            cache = base / "cache"
            workspace.mkdir()
            cache.mkdir()
            with patch.dict(os.environ, {ENV: str(cache)}), self.assertRaises(ToolFailure):
                Runtime(workspace, permission_mode="dangerous")

    def test_failed_ruleset_does_not_spawn_unconfined_command(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            cache = base / "cache"
            workspace.mkdir()
            cache.mkdir()
            with patch.dict(os.environ, {ENV: str(cache)}):
                runtime = Runtime(workspace)
                try:
                    with patch.object(server_module, "landlock_abi_version", return_value=3), patch.object(
                        server_module, "open_landlock_ruleset",
                        side_effect=ToolFailure("SANDBOX_UNAVAILABLE", "test failure", category="security"),
                    ), patch.object(server_module, "spawn_process") as spawn:
                        with self.assertRaises(ToolFailure):
                            runtime.exec_command({"cmd": "true"})
                        spawn.assert_not_called()
                    cache.rmdir()
                    with self.assertRaises(ToolFailure):
                        runtime.landlock_write_roots()
                finally:
                    runtime.close()

    @unittest.skipUnless(sys.platform == "linux", "Landlock requires Linux")
    def test_real_landlock_allows_only_configured_write_roots(self) -> None:
        status = server_module.landlock_status_payload()
        if not status["available"] or status["abi_version"] < 3:
            self.skipTest("Landlock ABI 3 or newer is required")
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            workspace, cache, readonly, sibling = (
                base / name for name in ("workspace", "cargo", "rustup", "sibling")
            )
            for directory in (workspace, cache, readonly, sibling):
                directory.mkdir()
                (directory / "original").write_text("original", encoding="utf-8")
            probe = workspace / "probe.py"
            probe.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "for name in sys.argv[1:]:\n"
                "    p = Path(name)\n"
                "    try:\n"
                "        (p / 'original').write_text('updated')\n"
                "        (p / 'new').touch()\n"
                "    except PermissionError:\n"
                "        print(p.name + ': denied')\n"
                "    else:\n"
                "        print(p.name + ': written')\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {ENV: str(cache), "CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS": str(readonly)}):
                runtime = Runtime(
                    workspace,
                    workspace_mutation=WorkspaceMutationPolicy(mode="structured-only"),
                )
                try:
                    env = runtime._command_env({})
                    env["SHELL"] = "/bin/sh"
                    fd = server_module.open_landlock_ruleset(
                        workspace,
                        server_module.guard_allow_roots(),
                        write_roots=runtime.landlock_write_roots(),
                        workspace_writable=False,
                    )
                    try:
                        cmd = shlex.join([sys.executable, "probe.py", *map(str, (cache, readonly, sibling, workspace))])
                        result = subprocess.run(
                            server_module.landlock_exec_argv(fd, cmd),
                            cwd=workspace, env=env, pass_fds=(fd,),
                            capture_output=True, text=True, timeout=10,
                        )
                    finally:
                        os.close(fd)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(set(result.stdout.splitlines()), {
                        "cargo: written", "rustup: denied", "sibling: denied", "workspace: denied",
                    })
                    self.assertEqual((cache / "original").read_text(), "updated")
                    for directory in (readonly, sibling, workspace):
                        self.assertEqual((directory / "original").read_text(), "original")
                        self.assertFalse((directory / "new").exists())
                finally:
                    runtime.close()


if __name__ == "__main__":
    unittest.main()
