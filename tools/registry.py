"""
工具注册中心

当前阶段: 仅提供工具风险等级映射
后续阶段: 完整的 BaseTool 注册、LangChain tool 转换、风险等级自动收集
"""

# ──────────────────────────────────────────────────────────────────
# 默认风险等级映射 (来自 dev-plan 5.2)
# ──────────────────────────────────────────────────────────────────

DEFAULT_TOOL_RISK: dict[str, str] = {
    # 文件操作
    "read_file":           "low",
    "glob_search":         "low",
    "grep_search":         "low",
    "write_file":          "medium",
    "edit_file":           "medium",
    # Shell
    "run_command":         "high",
    # MT-3000
    "analyze_source":      "low",
    "diff_summary":        "low",
    "generate_optimized":  "medium",
    "compile_device":      "medium",
    "apply_patch":         "medium",
    # 远程
    "ssh_command":         "high",
    "ssh_upload":          "high",
    "ssh_download":        "medium",
    "slurm_submit":        "high",
    "slurm_status":        "low",
    "slurm_fetch":         "low",
    # Agent 控制
    "ask_user":            "low",
    "plan_mode":           "low",
}

# 未注册工具的默认风险等级
DEFAULT_UNKNOWN_RISK = "medium"
