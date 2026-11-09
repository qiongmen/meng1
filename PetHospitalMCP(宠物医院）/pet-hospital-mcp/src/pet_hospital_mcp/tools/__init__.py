"""MCP 工具集合。

新增工具时只需在本包增加模块，并复用 rest_client、logging、errors 约定。
"""

from .list_pets import make_list_pets_tool

__all__ = ["make_list_pets_tool"]
