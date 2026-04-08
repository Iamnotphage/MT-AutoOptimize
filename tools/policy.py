"""工具风险策略 — 静态风险等级映射（供 tool_routing 按名称查表）"""

DEFAULT_TOOL_RISK: dict[str, str] = {
    # 文件操作
    "read_file":           "low",
    "ls":                  "low",
    "glob":                "low",
    "grep":                "low",
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
    "save_memory":         "low",
    "ask_user":            "low",
    "plan_mode":           "low",
}

DEFAULT_UNKNOWN_RISK = "medium"
