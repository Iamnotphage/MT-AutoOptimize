import asyncio

import pytest

from tools.base import BaseTool, ToolResult, ToolRiskLevel
from tools.registry import ToolRegistry
from tools.file_ops.read_file import ReadFileTool


# ── ReadFileTool ─────────────────────────────────────────────────

class TestReadFileTool:

    @pytest.fixture()
    def workspace(self, tmp_path):
        (tmp_path / "hello.txt").write_text("line1\nline2\nline3\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.c").write_text("int main() {}\n")
        return tmp_path

    @pytest.fixture()
    def tool(self, workspace):
        return ReadFileTool(workspace=workspace)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_read_full_file(self, tool):
        r = self._run(tool.execute(file_path="hello.txt"))
        assert r.success
        assert "line1" in r.output
        assert "line2" in r.output
        assert r.metadata["total_lines"] == 3

    def test_read_line_range(self, tool):
        r = self._run(tool.execute(file_path="hello.txt", start_line=2, end_line=2))
        assert r.success
        assert "line2" in r.output
        assert "line1" not in r.output

    def test_read_subdir(self, tool):
        r = self._run(tool.execute(file_path="sub/deep.c"))
        assert r.success
        assert "int main" in r.output

    def test_file_not_found(self, tool):
        r = self._run(tool.execute(file_path="nope.txt"))
        assert not r.success
        assert "不存在" in r.error

    def test_path_traversal_blocked(self, tool):
        r = self._run(tool.execute(file_path="../../etc/passwd"))
        assert not r.success
        assert "越界" in r.error

    def test_truncation(self, workspace):
        big = "\n".join(f"L{i}" for i in range(1000))
        (workspace / "big.txt").write_text(big)
        tool = ReadFileTool(workspace=workspace)
        r = self._run(tool.execute(file_path="big.txt"))
        assert r.success
        assert r.metadata["truncated"] is True
        assert "truncated" in r.output

    def test_schema_shape(self, tool):
        s = tool.schema
        assert s["type"] == "function"
        assert s["function"]["name"] == "read_file"
        assert "file_path" in s["function"]["parameters"]["properties"]


# ── ToolRegistry ─────────────────────────────────────────────────

class TestToolRegistry:

    @pytest.fixture()
    def registry(self, tmp_path):
        reg = ToolRegistry()
        reg.register(ReadFileTool(workspace=tmp_path))
        return reg

    def test_register_and_lookup(self, registry):
        assert "read_file" in registry
        assert registry.get("read_file") is not None
        assert len(registry) == 1

    def test_schemas_list(self, registry):
        schemas = registry.schemas
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "read_file"

    def test_execute_unknown_tool(self, registry):
        r = asyncio.get_event_loop().run_until_complete(
            registry.execute("no_such_tool", {})
        )
        assert not r.success
        assert "未知工具" in r.error

    def test_execute_with_validation(self, registry, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        r = asyncio.get_event_loop().run_until_complete(
            registry.execute("read_file", {"file_path": "a.txt"})
        )
        assert r.success
        assert "hello" in r.output

    def test_needs_confirmation(self, registry):
        assert registry.needs_confirmation("read_file") is False
        assert registry.needs_confirmation("unknown") is True
