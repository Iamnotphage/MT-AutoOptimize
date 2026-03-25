from tools.base import BaseTool, ToolResult, ToolRiskLevel
from tools.registry import ToolRegistry
from tools.file_ops.read_file import ReadFileTool
from tools.file_ops.write_file import WriteFileTool
from tools.file_ops.ls import LsTool

__all__ = [
    "BaseTool", "ToolResult", "ToolRiskLevel", "ToolRegistry",
    "ReadFileTool", "WriteFileTool", "LsTool",
    "create_default_tools",
]


def create_default_tools(*, workspace: str) -> list[BaseTool]:
    """集中创建所有内置工具，新增工具只需改这里"""
    return [
        ReadFileTool(workspace=workspace),
        WriteFileTool(workspace=workspace),
        LsTool(workspace=workspace),
    ]
