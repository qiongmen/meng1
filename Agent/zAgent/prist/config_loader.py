"""prist 配置加载器.

优先级：环境变量 > config.ini > 默认值
首次启动若无 config.ini，自动生成带 mock 信息的模板并标记 self.is_first_run。
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Optional

DEFAULT_SEARCH_BASE_URL = 'http://appbuilder.baidu.com/v2/ai_search'
DEFAULT_LLM_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_LLM_MODEL = 'gpt-4o-mini'

# 硬编码常量全部集中在这里
DEFAULT_LLM_TIMEOUT = 30
DEFAULT_SEARCH_TIMEOUT = 15
DEFAULT_SEARCH_MAX_REFS = 8
DEFAULT_MAX_TOOL_ROUNDS = 10
DEFAULT_CONTEXT_WINDOW = 128 * 1024   # 128k
DEFAULT_COMPACT_THRESHOLD = 0.80
DEFAULT_SESSION_DIR_NAME = '.agents/sessions'
DEFAULT_MCP_DISCOVERY_TIMEOUT = 15    # 秒


class PristConfig:
    """从 prist/config.ini 加载 LLM、搜索等配置。"""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        if config_path is None:
            config_path = self._locate_config()
        self.path = config_path
        self._parser = configparser.ConfigParser()

        if not self.path.is_file():
            self.is_first_run = True
            self._create_template()
        else:
            self.is_first_run = False

        self._parser.read(self.path, encoding='utf-8')

    # -- LLM -----------------------------------------------------------------

    @property
    def llm_api_key(self) -> str:
        env = os.environ.get('ZAGENT_LLM_API_KEY', '').strip()
        if env:
            return env
        return self._parser.get('llm', 'api_key', fallback='').strip()

    @property
    def llm_base_url(self) -> str:
        env = os.environ.get('ZAGENT_LLM_BASE_URL', '').strip()
        if env:
            return env
        return self._parser.get('llm', 'base_url', fallback=DEFAULT_LLM_BASE_URL).strip()

    @property
    def llm_model(self) -> str:
        env = os.environ.get('ZAGENT_LLM_MODEL', '').strip()
        if env:
            return env
        return self._parser.get('llm', 'model', fallback=DEFAULT_LLM_MODEL).strip()

    @property
    def llm_timeout(self) -> int:
        try:
            return int(self._parser.get('llm', 'timeout', fallback=str(DEFAULT_LLM_TIMEOUT)))
        except ValueError:
            return DEFAULT_LLM_TIMEOUT

    @property
    def context_window_size(self) -> int:
        try:
            return int(self._parser.get('llm', 'context_window', fallback=str(DEFAULT_CONTEXT_WINDOW)))
        except ValueError:
            return DEFAULT_CONTEXT_WINDOW

    @property
    def compact_threshold(self) -> float:
        try:
            return float(self._parser.get('llm', 'compact_threshold', fallback=str(DEFAULT_COMPACT_THRESHOLD)))
        except ValueError:
            return DEFAULT_COMPACT_THRESHOLD

    @property
    def max_tool_rounds(self) -> int:
        try:
            return int(self._parser.get('llm', 'max_tool_rounds', fallback=str(DEFAULT_MAX_TOOL_ROUNDS)))
        except ValueError:
            return DEFAULT_MAX_TOOL_ROUNDS

    # -- 搜索 ----------------------------------------------------------------

    @property
    def search_api_key(self) -> str:
        env = os.environ.get('ZAGENT_SEARCH_API_KEY', '').strip()
        if env:
            return env
        return self._parser.get('search', 'api_key', fallback='').strip()

    @property
    def search_base_url(self) -> str:
        env = os.environ.get('ZAGENT_SEARCH_BASE_URL', '').strip()
        if env:
            return env
        return self._parser.get('search', 'base_url', fallback=DEFAULT_SEARCH_BASE_URL).strip()

    @property
    def search_timeout(self) -> int:
        try:
            return int(self._parser.get('search', 'timeout', fallback=str(DEFAULT_SEARCH_TIMEOUT)))
        except ValueError:
            return DEFAULT_SEARCH_TIMEOUT

    @property
    def search_max_refs(self) -> int:
        try:
            return int(self._parser.get('search', 'max_refs', fallback=str(DEFAULT_SEARCH_MAX_REFS)))
        except ValueError:
            return DEFAULT_SEARCH_MAX_REFS

    @property
    def search_summary(self) -> str:
        return self._parser.get('search', 'summary_field', fallback='answer').strip()

    # -- Agent 行为 ---------------------------------------------------------

    @property
    def session_dir(self) -> Path:
        return Path.cwd() / DEFAULT_SESSION_DIR_NAME

    @property
    def mcp_discovery_timeout(self) -> int:
        try:
            return int(self._parser.get('agent', 'mcp_discovery_timeout',
                                         fallback=str(DEFAULT_MCP_DISCOVERY_TIMEOUT)))
        except ValueError:
            return DEFAULT_MCP_DISCOVERY_TIMEOUT

    @property
    def extra_skills_paths(self) -> list[Path]:
        """额外的技能目录（逗号分隔，可写绝对路径或相对于 cwd）。"""
        raw = self._parser.get('agent', 'extra_skills_paths', fallback='').strip()
        if not raw:
            return []
        paths: list[Path] = []
        for item in raw.split(','):
            item = item.strip()
            if not item:
                continue
            p = Path(item) if Path(item).is_absolute() else Path.cwd() / item
            if p.is_dir():
                paths.append(p)
        return paths

    # -- 帮助 ----------------------------------------------------------------

    @staticmethod
    def _locate_config() -> Path:
        return Path.cwd() / 'prist' / 'config.ini'

    def _create_template(self) -> None:
        """生成带 mock 信息的完整模板。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser()

        cp['llm'] = {
            'api_key': 'sk-your-mock-api-key',
            'base_url': DEFAULT_LLM_BASE_URL,
            'model': DEFAULT_LLM_MODEL,
            'timeout': str(DEFAULT_LLM_TIMEOUT),
            'context_window': str(DEFAULT_CONTEXT_WINDOW),
            'compact_threshold': str(DEFAULT_COMPACT_THRESHOLD),
            'max_tool_rounds': str(DEFAULT_MAX_TOOL_ROUNDS),
        }

        cp['search'] = {
            'api_key': 'bce-v3/ALTAK-mock-search-key',
            'base_url': DEFAULT_SEARCH_BASE_URL,
            'timeout': str(DEFAULT_SEARCH_TIMEOUT),
            'max_refs': str(DEFAULT_SEARCH_MAX_REFS),
        }

        cp['agent'] = {
            'mcp_discovery_timeout': str(DEFAULT_MCP_DISCOVERY_TIMEOUT),
        }

        with self.path.open('w', encoding='utf-8') as f:
            f.write("# 首次启动自动生成的模板，请填入真实值后重启。\n")
            f.write("# 任何一项留空或使用 mock 值，对应功能会静默降级或使用 mock 实现。\n\n")
            cp.write(f)

    def save_template(self) -> None:
        self._create_template()


def load_config() -> PristConfig:
    return PristConfig()
