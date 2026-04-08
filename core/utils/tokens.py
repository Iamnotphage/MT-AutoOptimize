"""Token 估算工具（参考 gemini-cli tokenCalculation.ts）

不引入 tokenizer 依赖，使用启发式方法估算 token 数量。
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """
    启发式 token 估算。

    - ASCII: ~4 字符/token
    - 非 ASCII (CJK): ~1.3 token/字符
    """
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return int(ascii_chars / 4 + non_ascii_chars * 1.3)
