"""zAgent 启动入口."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _build_agent():
    from core.factory import build_agent
    return build_agent()


def run_tui() -> None:
    from tui.app import run
    agent, warning = _build_agent()
    if warning:
        print(warning)
        print()
    print('zAgent · TUI 启动中…')
    run(agent, first_run_warning=warning)


def run_web(port: int = 8000) -> None:
    from web.app import run
    agent, warning = _build_agent()
    if warning:
        print(warning)
    print(f'zAgent · Web 启动中 → http://127.0.0.1:{port}')
    run(host='127.0.0.1', port=port)


def main() -> None:
    parser = argparse.ArgumentParser(prog='main.py', description='zAgent 启动器')
    parser.add_argument('mode', choices=['tui', 'web'], default='tui', nargs='?')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    if args.mode == 'tui':
        run_tui()
    elif args.mode == 'web':
        run_web(args.port)


if __name__ == '__main__':
    main()
