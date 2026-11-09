"""移动端桥接层 run() 占位函数。"""

from __future__ import annotations


def run(config_path: str | None = None) -> None:
    raise NotImplementedError(
        'mobile 前端尚未实现。'
        '与 tui/web 一样消费 Agent.send() 事件流即可，core 无需改动。'
    )
