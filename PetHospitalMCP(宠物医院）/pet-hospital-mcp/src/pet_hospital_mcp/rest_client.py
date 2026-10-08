"""Go 宠物医院 REST API 客户端 + Pydantic 数据模型。

- 严格输入模型 `ListPetsInput`：拒绝未知字段、NaN、Infinity、类型错误，
  species/status/sortBy/order 使用真实后端允许值，page>=1、1<=pageSize<=500、
  min/max 非负且 min<=max。
- 宽松响应模型（Pet/MedicalRecord/Treatment）：`extra="allow"` 兼容后端字段演进，
  并兼容 Go 的 records/charges 可能是 null 或数组的真实 JSON 表现。
- `PetHospitalClient`：基于 httpx.AsyncClient，GET /api/v1/pets，超时 + 有限重试，
  解析 Resp 信封，把上游异常映射为统一错误码的 ToolError。
"""

from __future__ import annotations

import math
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import (
    BACKEND_API_ERROR,
    BACKEND_INVALID_RESPONSE,
    BACKEND_TIMEOUT,
    BACKEND_UNAVAILABLE,
    ToolError,
)

# ---------------------------------------------------------------------------
# 后端真实允许值（来自 Go /api/v1/meta）
# ---------------------------------------------------------------------------
SPECIES_VALUES = ("犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他")
STATUS_VALUES = ("待就诊", "就诊中", "住院中", "已康复", "慢性病随访")
SORT_BY_VALUES = (
    "id", "name", "ownerName", "species", "doctor", "disease",
    "status", "totalCost", "visitCount", "createdAt", "updatedAt",
)
ORDER_VALUES = ("asc", "desc")


# ---------------------------------------------------------------------------
# 输入模型：严格校验
# ---------------------------------------------------------------------------
class ListPetsInput(BaseModel):
    """list_pets 工具的输入参数，严格校验。

    所有字段均为可选；不传则不会作为查询参数发送给后端。
    """

    model_config = ConfigDict(extra="forbid")

    q: str | None = None
    name: str | None = None
    ownerName: str | None = None
    ownerPhone: str | None = None
    species: Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"] | None = None
    doctor: str | None = None
    disease: str | None = None
    status: Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"] | None = None
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)
    sortBy: Literal[
        "id", "name", "ownerName", "species", "doctor", "disease",
        "status", "totalCost", "visitCount", "createdAt", "updatedAt"
    ] | None = None
    order: Literal["asc", "desc"] | None = None
    page: int | None = Field(default=None, ge=1)
    pageSize: int | None = Field(default=None, ge=1, le=500)

    @field_validator("min", "max", mode="after")
    @classmethod
    def _must_be_finite(cls, v: float | None) -> float | None:
        # 拒绝 NaN、Infinity（pydantic 默认不拒绝）
        if v is not None and not math.isfinite(v):
            raise ValueError("min/max 不允许 NaN 或 Infinity")
        return v

    @model_validator(mode="after")
    def _check_min_max(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min 不能大于 max")
        return self


# ---------------------------------------------------------------------------
# 响应模型：宽松（兼容后端字段演进与 null/数组）
# ---------------------------------------------------------------------------
class _LenientModel(BaseModel):
    """宽松基类：允许且忽略未知字段，兼容后端未来新增字段。"""

    model_config = ConfigDict(extra="allow")


class MedicalRecord(_LenientModel):
    id: str
    visitDate: str
    doctor: str
    diagnosis: str
    symptoms: str | None = None
    treatment: str | None = None
    prescription: list[str] | None = None
    weightKg: float | None = None
    temperature: float | None = None
    followUp: str | None = None
    charge: float
    createdAt: str


class Treatment(_LenientModel):
    id: str
    item: str
    category: str
    amount: float
    doctor: str | None = None
    date: str
    note: str | None = None


class Pet(_LenientModel):
    id: str
    name: str
    species: str
    breed: str | None = None
    gender: str | None = None
    ageMonths: int | None = None
    color: str | None = None
    chipNo: str | None = None
    ownerName: str
    ownerPhone: str
    ownerAddr: str | None = None
    doctor: str
    disease: str
    status: str
    allergy: str | None = None
    note: str | None = None
    # 兼容 Go nil slice 序列化为 null、空 slice 序列化为 []
    records: list[MedicalRecord] | None = None
    charges: list[Treatment] | None = None
    totalCost: float
    visitCount: int
    createdAt: str
    updatedAt: str


class ListPetsSuccess(BaseModel):
    """list_pets 成功输出，对应 Go API 成功响应中的 data。"""

    items: list[Pet]
    total: int
    page: int
    pageSize: int
    totalPages: int
    totalCost: float


class RespEnvelope(BaseModel):
    """Go API 响应信封。data 省略时为 None。"""

    model_config = ConfigDict(extra="allow")
    code: int
    message: str
    data: Any = None
    time: str | None = None


# ---------------------------------------------------------------------------
# REST 客户端
# ---------------------------------------------------------------------------
class PetHospitalClient:
    """异步 HTTP 客户端，封装对 Go 宠物医院 REST API 的调用。

    通过依赖注入 transport，便于测试用 httpx.MockTransport 替换真实网络。
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retries = max(0, retries)
        # transport 为 None 时走真实网络；测试注入 MockTransport
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PetHospitalClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def list_pets(self, params: dict[str, Any]) -> ListPetsSuccess:
        """调用 GET /api/v1/pets，返回校验后的 ListPetsSuccess。

        上游超时、连接异常、4xx/5xx、非法 JSON、模型不符均映射为 ToolError。
        超时和连接异常会按 retries 有限重试。
        """
        last_exc: Exception | None = None
        # 总尝试次数 = 1 + retries
        for attempt in range(self._retries + 1):
            try:
                response = await self._client.get("/api/v1/pets", params=params)
                break
            except httpx.TimeoutException as e:
                last_exc = e
                # 超时重试
                continue
            except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                last_exc = e
                # 连接异常重试
                continue
        else:
            # 全部重试均失败：区分超时与不可达
            assert last_exc is not None
            if isinstance(last_exc, httpx.TimeoutException):
                raise ToolError(BACKEND_TIMEOUT, "后端响应超时")
            raise ToolError(BACKEND_UNAVAILABLE, "无法连接到后端服务")

        # 4xx/5xx：后端业务错误，不重试已在此处返回
        if response.status_code >= 400:
            raise ToolError(
                BACKEND_API_ERROR,
                f"后端返回 HTTP {response.status_code}",
                details={"status_code": response.status_code},
            )

        # 解析 JSON
        try:
            raw = response.json()
        except Exception:
            raise ToolError(BACKEND_INVALID_RESPONSE, "后端返回非 JSON 内容")

        # 解析信封
        try:
            envelope = RespEnvelope.model_validate(raw)
        except Exception:
            raise ToolError(BACKEND_INVALID_RESPONSE, "后端响应不符合信封结构")

        # 信封 code 非 2xx：后端业务层报错
        if envelope.code < 200 or envelope.code >= 400:
            raise ToolError(
                BACKEND_API_ERROR,
                envelope.message or f"后端业务错误 code={envelope.code}",
                details={"code": envelope.code, "message": envelope.message},
            )

        # 解析 data 为 ListPetsSuccess
        if envelope.data is None:
            raise ToolError(BACKEND_INVALID_RESPONSE, "后端响应缺少 data 字段")
        try:
            return ListPetsSuccess.model_validate(envelope.data)
        except Exception:
            raise ToolError(BACKEND_INVALID_RESPONSE, "后端 data 不符合 list_pets 数据模型")
