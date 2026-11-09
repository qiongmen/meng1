"""OpenAI 兼容接口客户端（httpx + chat/completions + function calling）."""

from __future__ import annotations

import json
from typing import Any

import httpx

from prist.config_loader import PristConfig, load_config


class OpenAIClient:
    """极简 OpenAI 兼容 Chat Completions 客户端.

    只实现 Agent 需要的：messages + tools(函数定义) → LLM 回复(文本或 tool_calls)
    """

    def __init__(self, config: PristConfig | None = None) -> None:
        self.cfg = config or load_config()

    # ------------------------------------------------------------------
    def call(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        """发一次 chat/completions 请求，返回标准化响应 dict。

        成功：
          {'content': str, 'tool_calls': [...]}   # 可能两者都空
        失败（网络/鉴权等）：
          {'error': '...错误说明...'}
        """
        base_url = self.cfg.llm_base_url.rstrip('/')
        url = f'{base_url}/chat/completions'

        if not self.cfg.llm_api_key or '你的' in self.cfg.llm_api_key:
            return {
                'error': (
                    '未配置 LLM API Key。\n'
                    '请在 prist/config.ini 的 [llm] 小节填写 api_key，'
                    '或设置环境变量 ZAGENT_LLM_API_KEY。'
                )
            }

        payload: dict[str, Any] = {
            'model': self.cfg.llm_model,
            'messages': messages,
            'max_tokens': 2048,
            'temperature': 0.7,
        }
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto'

        try:
            with httpx.Client(timeout=self.cfg.llm_timeout) as client:
                resp = client.post(
                    url,
                    headers={
                        'Authorization': f'Bearer {self.cfg.llm_api_key}',
                        'Content-Type': 'application/json',
                    },
                    json=payload,
                )
        except httpx.ConnectTimeout:
            return {'error': f'LLM 请求超时（{self.cfg.llm_timeout}s）。base_url={base_url}'}
        except httpx.ConnectError:
            return {'error': f'无法连接到 LLM 服务 {base_url}'}
        except httpx.HTTPError as exc:
            return {'error': f'LLM 请求失败: {type(exc).__name__}: {exc}'}

        if resp.status_code in (401, 403):
            return {'error': f'LLM 鉴权失败（HTTP {resp.status_code}），请检查 api_key 是否正确。'}
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:400]
            return {'error': f'LLM 返回错误（HTTP {resp.status_code}）: {detail}'}

        try:
            data = resp.json()
        except Exception:
            return {'error': f'LLM 响应解析失败: {resp.text[:400]}'}

        # 业务错误
        if 'error' in data and isinstance(data['error'], dict):
            return {'error': f"LLM API 错误: {data['error'].get('code', '')} - {data['error'].get('message', '')}"}

        return self._normalize(data)

    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(data: dict) -> dict[str, Any]:
        """把不同 OpenAI 兼容实现的返回统一成 {'content', 'tool_calls'} 格式。"""
        choices = data.get('choices') or []
        if not choices:
            content = (data.get('content') or data.get('answer') or '').strip()
            if content:
                return {'content': content, 'tool_calls': []}
            return {'content': '', 'tool_calls': []}

        msg = choices[0].get('message') or {}
        content = msg.get('content') or ''
        if isinstance(content, list):
            # 某些实现会返回 content 为 list（text + image）
            content = '\n'.join(
                c.get('text', '') for c in content if isinstance(c, dict)
            )
        content = content.strip()

        raw_tool_calls = msg.get('tool_calls') or []
        tool_calls: list[dict] = []
        for tc in raw_tool_calls:
            func = tc.get('function', tc)
            arguments = func.get('arguments', '{}')
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {'_raw': arguments}
            tool_calls.append({
                'id': tc.get('id', f'call_{len(tool_calls)}'),
                'name': func.get('name', ''),
                'arguments': arguments,
            })

        return {'content': content, 'tool_calls': tool_calls}


# ---- 工厂函数 ----------------------------------------------------------------

def make_llm_client():
    """构建一个 llm_client 可调用对象，可直接传给 Agent(llm_client=...)。"""
    cli = OpenAIClient()

    def _client(messages: list[dict], tools: list[dict]) -> Any:
        result = cli.call(messages, tools)
        if 'error' in result:
            # 把错误返回成文本，让 LLM 自己不会被触发
            # Agent 这边目前期望 str 或 {'content', 'tool_calls'}
            return {'content': f'[LLM 客户端错误] {result["error"]}', 'tool_calls': []}
        return result

    return _client
