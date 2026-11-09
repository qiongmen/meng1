"""Agent 构建入口."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agent import Agent


def build_agent(config_path: Optional[str] = None) -> tuple[Agent, Optional[str]]:
    """构建 Agent，返回 (agent, warning_or_None)。

    - 若 [llm] 小节配好了 api_key → 使用真实 OpenAI 兼容接口
    - 否则回退 mock（开发调试用）
    - config_loader 会自动从 config.ini 读取所有 Agent 参数
    - 首次启动（自动生成了模板）时返回 warning 提示用户填写真实值
    """
    from prist.config_loader import load_config
    cfg = load_config()

    # LLM 客户端：优先 prist 配置
    llm_client = None
    try:
        from prist.llm_client import make_llm_client
        if cfg.llm_api_key and 'mock' not in cfg.llm_api_key.lower():
            llm_client = make_llm_client()
    except Exception:
        pass

    agent = Agent(
        llm_client=llm_client,
        context_window_size=cfg.context_window_size,
        compact_threshold=cfg.compact_threshold,
        max_tool_rounds=cfg.max_tool_rounds,
    )

    # 首次启动警告
    warning = None
    if cfg.is_first_run:
        warning = (
            f'⚠ 首次启动：已在 {cfg.path} 生成模板。\n'
            f'  请填入真实的 LLM API Key / base_url / model 后重启。\n'
            f'  当前未配置真实 LLM，Agent 将使用 mock 实现。'
        )

    return agent, warning
