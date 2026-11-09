"""网络工具：searchWeb（百度千帆智能搜索） + fetchWeb（curl + trafilatura 提取）."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

import httpx

from prist.config_loader import load_config
from core.registry import ToolSpec


# ---------------------------------------------------------------------------
# searchWeb —— 百度千帆 AppBuilder /v2/ai_search
# ---------------------------------------------------------------------------

def _search_web_impl(query: str, max_refs: int | None = None) -> str:
    """调用百度千帆智能搜索，返回整理后的摘要 + 参考来源。"""
    cfg = load_config()

    if not cfg.search_api_key or '你的' in cfg.search_api_key:
        return (
            '未配置 search API Key。\n'
            '请在 prist/config.ini 的 [search] 小节填写 api_key，'
            '或设置环境变量 ZAGENT_SEARCH_API_KEY。'
        )

    if not query.strip():
        return '查询内容为空，请提供有效的搜索关键词或问题。'

    top_n = max_refs or cfg.search_max_refs

    try:
        with httpx.Client(timeout=cfg.search_timeout) as client:
            resp = client.post(
                cfg.search_base_url,
                headers={
                    'Authorization': f'Bearer {cfg.search_api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'messages': [
                        {'role': 'user', 'content': query},
                    ],
                },
            )
    except httpx.ConnectTimeout:
        return f'搜索请求超时（{cfg.search_timeout}s），请检查网络或 config.ini 中的 base_url。'
    except httpx.ConnectError:
        return f'无法连接到搜索服务 {cfg.search_base_url}，请检查网络。'
    except httpx.HTTPError as exc:
        return f'搜索请求失败: {type(exc).__name__}: {exc}'

    if resp.status_code == 401 or resp.status_code == 403:
        return f'API 鉴权失败（HTTP {resp.status_code}），请检查 prist/config.ini 中的 api_key 是否正确。'
    if resp.status_code != 200:
        try:
            detail = resp.text[:300]
        except Exception:
            detail = ''
        return f'搜索服务返回错误（HTTP {resp.status_code}）: {detail}'

    try:
        data = resp.json()
    except Exception:
        return f'搜索响应解析失败，原始内容: {resp.text[:300]}'

    # API 业务错误
    if 'code' in data and data.get('code') and data.get('code') not in (200, '200', 'Success'):
        return f'搜索 API 返回错误: {data.get("code")} - {data.get("message", "")}'

    references = data.get('references') or []
    if not references:
        return '未找到相关搜索结果，请换个关键词试试。'

    # 取前 top_n 条
    refs = references[:top_n]

    lines = [f'搜索 query: {query}', f'共 {len(refs)} 条结果（取自 {len(references)} 条）', '']

    # 汇总摘要（取每条 snippet/content 的前 100 字）
    summary_parts = []
    for r in refs:
        snippet = (r.get('snippet') or r.get('content') or r.get('markdown_content') or '').strip()
        if snippet:
            summary_parts.append(snippet[:150])
    if summary_parts:
        lines.append('【搜索摘要】')
        for i, s in enumerate(summary_parts[:5], 1):
            lines.append(f'{i}. {s}{"…" if len(s) >= 150 else ""}')
        lines.append('')

    lines.append('【参考来源】')
    for r in refs:
        title = (r.get('title') or '(无标题)').strip()
        url = (r.get('url') or '').strip()
        date = (r.get('date') or '').strip()
        site = (r.get('website') or r.get('icon') or '').strip()
        lines.append(f'- {title}')
        if date:
            lines.append(f'  日期: {date}')
        if site:
            lines.append(f'  站点: {site}')
        lines.append(f'  URL: {url}')

    return '\n'.join(lines)


SEARCH_WEB_SPEC = ToolSpec(
    name='searchWeb',
    description=(
        '通过百度千帆智能搜索获取互联网上的实时信息。'
        '适合需要最新资讯、事实核查、新闻动态等场景。返回搜索摘要和参考来源。'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'query': {'type': 'string', 'description': '搜索关键词或自然语言问题'},
            'max_refs': {'type': 'integer', 'description': '最多返回的参考来源数（默认 8）'},
        },
        'required': ['query'],
    },
    handler=_search_web_impl,
)


# ---------------------------------------------------------------------------
# fetchWeb —— curl 获取 HTML + trafilatura 提取 Markdown
# ---------------------------------------------------------------------------

def _find_curl() -> str:
    """定位 curl 可执行文件路径。"""
    candidates = ['curl.exe', 'curl']
    for name in candidates:
        try:
            result = subprocess.run([name, '--version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return name
        except Exception:
            continue
    return ''


def _fetch_web_impl(url: str, timeout: int = 30) -> str:
    """用 curl 抓 HTML，再用 trafilatura 提取正文为 Markdown。"""
    if not url.strip():
        return 'URL 不能为空。'

    curl = _find_curl()
    if not curl:
        return '未找到 curl 可执行文件，请安装 curl 后重试。'

    # 1) curl 抓 HTML —— 用参数列表，不用 shell=True
    try:
        result = subprocess.run(
            [
                curl,
                '-L',                          # 跟随重定向
                '--max-time', str(timeout),    # 总超时
                '-s',                          # 静默
                '-o', '-',                     # 输出到 stdout
                '-w', '\n__HTTP_CODE__:%{http_code}',  # 末尾输出状态码
                '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) zAgent-FetchWeb',
                '--compressed',                # 支持 gzip
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 10,  # 略大于 curl --max-time
        )
    except subprocess.TimeoutExpired:
        return f'抓取超时（{timeout + 10}s）。'
    except Exception as exc:
        return f'curl 执行失败: {type(exc).__name__}: {exc}'

    # 分离 HTTP code 和 HTML
    stdout = result.stdout or ''
    parts = stdout.rsplit('\n__HTTP_CODE__:', 1)
    html = parts[0] if len(parts) == 2 else stdout
    http_code = parts[1].strip() if len(parts) == 2 else '0'

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return f'curl 请求失败（exit {result.returncode}）: {stderr[:200]}'

    if not html.strip():
        return f'网页内容为空（HTTP {http_code}），可能需要登录或被反爬。'

    # 2) trafilatura 提取正文
    try:
        import trafilatura  # noqa: WPS433
    except ImportError:
        return (
            '未安装 trafilatura，请执行: pip install trafilatura\n'
            f'HTTP {http_code}，原始 HTML 长度 {len(html)}'
        )

    try:
        markdown = trafilatura.extract(
            html,
            output_format='markdown',
            include_links=True,
            include_tables=True,
            deduplicate=True,
            favor_precision=True,
        )
    except Exception as exc:
        return f'trafilatura 解析失败: {type(exc).__name__}: {exc}'

    if not markdown or not markdown.strip():
        # 回退：纯文本
        try:
            markdown = trafilatura.extract(
                html,
                output_format='txt',
                favor_precision=True,
            )
        except Exception:
            markdown = None

    if not markdown or not markdown.strip():
        return f'无法从该网页提取正文内容（HTTP {http_code}）。可能是 JavaScript 动态渲染的页面。'

    # 3) 组装返回
    out = [
        f'来源 URL: {url}',
        f'HTTP 状态码: {http_code}',
        f'正文长度: {len(markdown)} 字符',
        '',
        markdown.strip(),
    ]
    return '\n'.join(out)


FETCH_WEB_SPEC = ToolSpec(
    name='fetchWeb',
    description=(
        '抓取指定网页的主要内容，自动去除导航、广告、页脚，返回 Markdown 格式正文。'
        '适合深度阅读单篇网页、提取新闻/博客/文档的核心内容。'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'url': {'type': 'string', 'description': '要抓取的网页 URL（需 http:// 或 https:// 开头）'},
            'timeout': {'type': 'integer', 'description': '超时秒数（默认 30）'},
        },
        'required': ['url'],
    },
    handler=_fetch_web_impl,
)


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def register_web_tools(registry) -> None:
    """把 searchWeb + fetchWeb 注册到 ToolRegistry。"""
    registry.register(SEARCH_WEB_SPEC)
    registry.register(FETCH_WEB_SPEC)
