"""
Agent 系统提示词模板与组装

职责: 根据当前 AgentState + 可用工具, 生成完整的 system prompt
"""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT_TEMPLATE = """\
你是 MT-3000 超算平台的代码优化 Agent。你的任务是帮助用户分析、优化和编译面向 MT-3000 平台的 C/C++ 代码。

## 能力
- 读取和分析源代码文件
- 判断代码适合 AM (阵列机向量化) 还是 SM (标量机缓存优化)
- 生成优化后的代码 (向量化 / 缓存优化)
- 使用 MT-3000 交叉编译工具链编译代码
- 根据编译错误自动修复并重试
{tool_section}
{context_section}
## 工作原则
1. 先理解用户需求，必要时通过工具读取文件获取上下文
2. 逐步完成优化任务，每一步给出清晰的推理
3. 遇到编译错误时分析原因并修复
4. 用中文回答用户问题

如果需要使用工具，请通过 function calling 调用。当任务完成或不需要工具时，直接给出文本回答。\
"""


def _format_tool_section(tool_schemas: list[dict[str, Any]]) -> str:
    """将 OpenAI function schemas 渲染为 Markdown 列表"""
    if not tool_schemas:
        return ""
    lines = ["", "## 可用工具"]
    for schema in tool_schemas:
        func = schema.get("function", schema)
        name = func.get("name", "")
        desc = func.get("description", "")
        lines.append(f"- **{name}**: {desc}")
    return "\n".join(lines) + "\n"


def _format_context_section(state: dict[str, Any]) -> str:
    """从 AgentState 中提取 MT-3000 运行上下文"""
    parts: list[str] = []
    if state.get("optimization_mode"):
        parts.append(f"- 优化模式: {state['optimization_mode']}")
    if state.get("source_file"):
        parts.append(f"- 源文件: {state['source_file']}")
    if state.get("working_directory"):
        parts.append(f"- 工作目录: {state['working_directory']}")
    if not parts:
        return ""
    return "\n## 当前上下文\n" + "\n".join(parts) + "\n"


def build_system_prompt(
    state: dict[str, Any],
    tool_schemas: list[dict[str, Any]] | None = None,
) -> str:
    """
    组装完整的系统提示词

    Args:
        state: AgentState (或兼容 dict)
        tool_schemas: OpenAI function-calling 格式的工具 schema 列表
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        tool_section=_format_tool_section(tool_schemas or []),
        context_section=_format_context_section(state),
    )
