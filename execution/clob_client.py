"""Polymarket CLOB API 客户端.

提供订单簿查询、下单、撤单、持仓查询等功能。
认证方式：L2 HMAC-SHA256 (api_key / api_secret / passphrase).

注意：下单操作还需要 EIP-712 私钥签名（L1），本模块假定签名由外部完成
或在生产环境中集成 py-clob-client 的签名逻辑。
"""

import hashlib
import hmac
import base64
import time
import os
import sys
from typing import Optional

import requests


class PolymarketAPIError(Exception):
    """Polymarket API 调用失败."""

    def __init__(self, status_code: int, message: str, response_body: str = ""):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"HTTP {status_code}: {message}")


class PolymarketClient:
    """Polymarket CLOB (Central Limit Order Book) API 客户端.

    Parameters
    ----------
    api_key : str
        L2 API Key (从 create_or_derive_api_creds 获得).
    api_secret : str
        L2 API Secret (base64 编码).
    api_passphrase : str
        L2 API Passphrase.
    base_url : str, optional
        CLOB API 基础地址，默认 https://clob.polymarket.com.
    private_key : str, optional
        EVM 私钥 hex 字符串，用于 EIP-712 订单签名（下单必需）.
    funder : str, optional
        资金地址 (proxy wallet address)，下单时使用.
    """

    BASE_URL = "https://clob.polymarket.com"

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
        base_url: Optional[str] = None,
        private_key: str = "",
        funder: str = "",
    ):
        # 支持从环境变量读取
        self.api_key = api_key or os.getenv("POLYMARKET_API_KEY", "")
        self.api_secret = api_secret or os.getenv("POLYMARKET_API_SECRET", "")
        self.api_passphrase = (
            api_passphrase or os.getenv("POLYMARKET_API_PASSPHRASE", "")
        )
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY", "")
        self.funder = funder or os.getenv("POLYMARKET_FUNDER", "")

        if not (self.api_key and self.api_secret and self.api_passphrase):
            print(
                "⚠️  [PolymarketClient] API 凭据不完整，"
                "查询类操作可用公开端点，但交易操作需要完整配置。",
                file=sys.stderr,
            )
            self._authenticated = False
        else:
            self._authenticated = True

        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ── HMAC 签名 ──────────────────────────────────────────────

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """生成 HMAC-SHA256 签名 (L2 认证).

        签名消息 = timestamp + method + path + body
        """
        message = f"{timestamp}{method.upper()}{path}{body}"
        mac = hmac.new(
            base64.urlsafe_b64decode(self.api_secret + "=="),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _auth_headers(
        self, method: str, path: str, body: str = ""
    ) -> dict[str, str]:
        """构造 L2 认证请求头."""
        if not self._authenticated:
            raise PolymarketAPIError(
                401, "API 凭据未配置，无法执行认证操作"
            )
        ts = str(int(time.time()))
        return {
            "POLY_ADDRESS": self.funder,
            "POLY_SIGNATURE": self._sign(ts, method, path, body),
            "POLY_TIMESTAMP": ts,
            "POLY_API_KEY": self.api_key,
            "POLY_PASSPHRASE": self.api_passphrase,
        }

    # ── 内部请求方法 ──────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        auth: bool = False,
    ) -> dict | list:
        """发送 HTTP 请求到 CLOB API.

        Parameters
        ----------
        method : str
            HTTP 方法 (GET, POST, DELETE 等).
        path : str
            API 路径，如 "/book".
        params : dict, optional
            URL 查询参数.
        json_body : dict, optional
            JSON 请求体.
        auth : bool
            是否附带 L2 认证头.

        Returns
        -------
        dict | list
            解析后的 JSON 响应.
        """
        url = f"{self.base_url}{path}"
        headers = {}
        body_str = ""

        if json_body:
            import json as _json
            body_str = _json.dumps(json_body, separators=(",", ":"))

        if auth:
            headers.update(self._auth_headers(method, path, body_str))

        resp = self._session.request(
            method=method.upper(),
            url=url,
            params=params,
            data=body_str if body_str else None,
            headers=headers,
            timeout=15,
        )

        if resp.status_code >= 400:
            raise PolymarketAPIError(
                resp.status_code,
                resp.reason or "Unknown error",
                resp.text[:500],
            )

        if resp.status_code == 204 or not resp.text:
            return {}
        return resp.json()

    # ── 公开端点 ──────────────────────────────────────────────

    def get_orderbook(self, token_id: str) -> dict:
        """获取指定 token 的订单簿 (bids / asks).

        Parameters
        ----------
        token_id : str
            Token ID (condition_id 对应的 token hash).

        Returns
        -------
        dict
            包含 ``bids`` 和 ``asks`` 列表，每项有 ``price`` 和 ``size``.
            例: {"bids": [{"price": "0.65", "size": "100"}], "asks": [...]}
        """
        return self._request("GET", "/book", params={"token_id": token_id})

    def get_midpoint(self, token_id: str) -> dict:
        """获取中间价.

        Parameters
        ----------
        token_id : str
            Token ID.

        Returns
        -------
        dict
            {"mid": "0.655"}
        """
        return self._request("GET", "/midpoint", params={"token_id": token_id})

    def get_price(self, token_id: str, side: str = "BUY") -> dict:
        """获取最优价格.

        Parameters
        ----------
        token_id : str
            Token ID.
        side : str
            "BUY" (最优 ask) 或 "SELL" (最优 bid).

        Returns
        -------
        dict
            {"price": "0.66"}
        """
        return self._request(
            "GET", "/price", params={"token_id": token_id, "side": side}
        )

    # ── 交易端点 (需要 L2 认证) ───────────────────────────────

    def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
    ) -> dict:
        """下单.

        ⚠️ 下单操作需要 EIP-712 签名 (L1)，即需要 private_key。
        本方法会尝试使用 py-clob-client (如果已安装) 进行签名；
        否则手动构造 order payload 并签名。

        Parameters
        ----------
        token_id : str
            Token ID.
        side : str
            "BUY" 或 "SELL".
        price : float
            价格 (0.01 ~ 0.99).
        size : float
            数量 (USDC 金额).
        order_type : str
            "GTC" (Good Till Cancelled) 或 "IOC" (Immediate or Cancel).

        Returns
        -------
        dict
            下单结果，包含 ``orderID`` 等字段.

        Raises
        ------
        PolymarketAPIError
            API 返回非 200 时.
        ValueError
            参数不合法时.
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side 必须是 'BUY' 或 'SELL'，收到 '{side}'")
        if not (0.01 <= price <= 0.99):
            raise ValueError(f"price 必须在 0.01 ~ 0.99 之间，收到 {price}")
        if size <= 0:
            raise ValueError(f"size 必须 > 0，收到 {size}")
        if order_type not in ("GTC", "IOC"):
            raise ValueError(f"order_type 必须是 'GTC' 或 'IOC'，收到 '{order_type}'")

        # 尝试用 py-clob-client 下单（推荐方式）
        try:
            return self._place_order_via_sdk(token_id, side, price, size, order_type)
        except ImportError:
            pass

        # 手动构造（需要额外的 EIP-712 签名逻辑）
        raise PolymarketAPIError(
            501,
            "手动 EIP-712 签名尚未实现。请安装 py-clob-client: "
            "pip install py-clob-client，并配置 POLYMARKET_PRIVATE_KEY.",
        )

    def _place_order_via_sdk(
        self, token_id: str, side: str, price: float,
        size: float, order_type: str,
    ) -> dict:
        """使用 py-clob-client SDK 下单."""
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY, SELL

        if not self.private_key:
            raise PolymarketAPIError(
                400, "下单需要配置 POLYMARKET_PRIVATE_KEY 环境变量"
            )

        sdk_client = ClobClient(
            host=self.base_url,
            key=self.private_key,
            chain_id=137,
            creds={
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "passphrase": self.api_passphrase,
            },
            funder=self.funder or None,
        )

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY if side == "BUY" else SELL,
        )

        signed_order = sdk_client.create_order(order_args)

        if order_type == "IOC":
            resp = sdk_client.post_order(signed_order, order_type="IOC")
        else:
            resp = sdk_client.post_order(signed_order)

        return resp

    def cancel_order(self, order_id: str) -> dict:
        """取消订单.

        Parameters
        ----------
        order_id : str
            订单 ID.

        Returns
        -------
        dict
            取消结果.
        """
        return self._request(
            "DELETE", "/order", json_body={"order_id": order_id}, auth=True
        )

    def cancel_all(self) -> dict:
        """取消所有未成交订单.

        Returns
        -------
        dict
            取消结果.
        """
        return self._request("DELETE", "/orders", auth=True)

    def get_open_orders(self) -> list[dict]:
        """获取当前未成交订单.

        Returns
        -------
        list[dict]
            订单列表.
        """
        result = self._request("GET", "/orders", auth=True)
        return result if isinstance(result, list) else []

    # ── 持仓与余额 ────────────────────────────────────────────

    def get_positions(self, limit: int = 100) -> list[dict]:
        """获取当前持仓.

        Returns
        -------
        list[dict]
            持仓列表，每项包含 ``asset``, ``size``, ``avgPrice`` 等字段.
        """
        params = {}
        if self.funder:
            params["user"] = self.funder
        params["limit"] = limit
        result = self._request("GET", "/positions", params=params, auth=True)
        return result if isinstance(result, list) else []

    def get_balance(self) -> float:
        """获取 USDC 余额.

        通过查询所有持仓的 cash token (USDC) 数量近似获取余额。

        Returns
        -------
        float
            USDC 余额.
        """
        try:
            positions = self.get_positions()
            for pos in positions:
                if pos.get("asset", "").lower() in ("usdc", "") and pos.get(
                    "proxyWallet"
                ):
                    return float(pos.get("size", 0))
            # 没有单独的 cash position，返回 0
            return 0.0
        except Exception:
            return 0.0

    # ── 辅助方法 ──────────────────────────────────────────────

    def is_authenticated(self) -> bool:
        """是否已配置 API 凭据."""
        return self._authenticated

    def has_signing_key(self) -> bool:
        """是否配置了私钥（下单必需）."""
        return bool(self.private_key)
