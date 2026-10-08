"""测试公共夹具：用 httpx.MockTransport 替换真实 Go 后端。

禁止测试时访问真实 Go 服务，全部通过 MockTransport 构造响应。
辅助函数（样本数据、信封构造）见 tests/helpers.py。
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from pet_hospital_mcp.config import Config
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.tools import make_list_pets_tool

from helpers import DEFAULT_BASE_URL, make_mock_transport


@pytest.fixture
def base_url() -> str:
    return DEFAULT_BASE_URL


@pytest.fixture
def config(base_url: str) -> Config:
    return Config(
        pet_hospital_base_url=base_url,
        mcp_host="127.0.0.1",
        mcp_port=8000,
        backend_timeout=5.0,
        backend_retries=2,
    )


@pytest.fixture
def make_client(base_url: str) -> Callable[..., PetHospitalClient]:
    """工厂：传入 transport handler 返回绑定了 MockTransport 的客户端。"""

    def _factory(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        timeout: float = 5.0,
        retries: int = 2,
    ) -> PetHospitalClient:
        return PetHospitalClient(
            base_url=base_url,
            timeout=timeout,
            retries=retries,
            transport=make_mock_transport(handler),
        )

    return _factory


@pytest.fixture
def make_tool(make_client) -> Callable[..., tuple]:
    """工厂：传入 transport handler，返回 (tool, client)。"""

    def _factory(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        timeout: float = 5.0,
        retries: int = 2,
    ):
        client = make_client(handler, timeout=timeout, retries=retries)
        tool = make_list_pets_tool(client)
        return tool, client

    return _factory
