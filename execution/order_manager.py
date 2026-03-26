"""订单管理器 — 将策略信号转化为实际订单.

负责：价格确认、滑点检查、下单执行、平仓、订单跟踪。
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .clob_client import PolymarketClient, PolymarketAPIError


class OrderManagerError(Exception):
    """订单管理异常."""


class OrderManager:
    """Polymarket 订单管理器.

    封装下单流程，提供信号执行、平仓、订单查询等高层接口。

    Parameters
    ----------
    client : PolymarketClient
        CLOB API 客户端实例.
    max_slippage : float
        最大允许滑点 (0.0 ~ 1.0)，默认 0.02 (2%).
    trade_log_path : str, optional
        交易日志文件路径，默认 ``execution/trade_log.jsonl``.
    """

    def __init__(
        self,
        client: PolymarketClient,
        max_slippage: float = 0.02,
        trade_log_path: Optional[str] = None,
    ):
        if not 0 < max_slippage < 1:
            raise ValueError(f"max_slippage 必须在 (0, 1) 之间，收到 {max_slippage}")

        self.client = client
        self.max_slippage = max_slippage
        self._log_path = Path(
            trade_log_path or Path(__file__).parent / "trade_log.jsonl"
        )

    # ── 核心：执行信号 ────────────────────────────────────────

    def execute_signal(self, signal: dict) -> dict:
        """执行策略信号.

        Parameters
        ----------
        signal : dict
            必须包含:
            - ``token_id``: str — 目标 token ID
            - ``direction``: str — "yes" (买) 或 "no" (卖)
            - ``amount``: float — 下单金额 (USDC)
            可选:
            - ``max_price``: float — 最高可接受价格 (仅 direction="yes")
            - ``min_price``: float — 最低可接受价格 (仅 direction="no")
            - ``order_type``: str — "GTC" 或 "IOC"，默认 "GTC"

        Returns
        -------
        dict
            下单结果，包含 ``order_id``, ``status``, ``executed_price`` 等.

        Raises
        ------
        OrderManagerError
            信号参数错误或滑点超限.
        """
        # ── 参数校验 ──
        token_id = signal.get("token_id")
        direction = signal.get("direction", "").lower()
        amount = signal.get("amount", 0)
        order_type = signal.get("order_type", "GTC")

        if not token_id:
            raise OrderManagerError("signal 缺少 token_id")
        if direction not in ("yes", "no"):
            raise OrderManagerError(
                f"direction 必须是 'yes' 或 'no'，收到 '{direction}'"
            )
        if not amount or amount <= 0:
            raise OrderManagerError(f"amount 必须 > 0，收到 {amount}")

        # ── 1. 查 orderbook ──
        try:
            book = self.client.get_orderbook(token_id)
        except PolymarketAPIError as e:
            raise OrderManagerError(f"获取 orderbook 失败: {e}") from e

        side = "BUY" if direction == "yes" else "SELL"
        ref_price = self._best_price(book, side)

        if ref_price is None:
            raise OrderManagerError(
                f"orderbook 为空或无法获取 {'ask' if side == 'BUY' else 'bid'} 价格"
            )

        # ── 2. 滑点检查 ──
        expected_price = ref_price
        if "max_price" in signal and direction == "yes":
            expected_price = min(ref_price, signal["max_price"])
        if "min_price" in signal and direction == "no":
            expected_price = max(ref_price, signal["min_price"])

        slippage = abs(ref_price - expected_price)
        if slippage > self.max_slippage:
            raise OrderManagerError(
                f"滑点 {slippage:.4f} 超过容忍范围 {self.max_slippage:.4f} "
                f"(当前价格={ref_price:.4f}, 目标价格={expected_price:.4f})"
            )

        # ── 3. 计算下单价格 ──
        # 买单在 ref_price 微微加一点以提高成交概率，卖单微减
        if side == "BUY":
            order_price = min(ref_price + 0.001, 0.99)
        else:
            order_price = max(ref_price - 0.001, 0.01)

        order_price = round(order_price, 2)  # Polymarket 要求 2 位小数

        # ── 4. 下单 ──
        try:
            result = self.client.place_order(
                token_id=token_id,
                side=side,
                price=order_price,
                size=amount,
                order_type=order_type,
            )
        except PolymarketAPIError as e:
            raise OrderManagerError(f"下单失败: {e}") from e

        # ── 5. 记录 ──
        trade_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "token_id": token_id,
            "direction": direction,
            "side": side,
            "amount": amount,
            "order_price": order_price,
            "ref_price": ref_price,
            "slippage": round(slippage, 6),
            "order_type": order_type,
            "result": result,
        }
        self._log_trade(trade_record)

        return {
            "status": "submitted",
            "order_id": result.get("orderID", result.get("order_id", "")),
            "side": side,
            "price": order_price,
            "size": amount,
            "order_type": order_type,
            "ref_price": ref_price,
            "slippage": round(slippage, 6),
            "raw_response": result,
        }

    # ── 平仓 ──────────────────────────────────────────────────

    def close_position(self, market_id: str, pct: float = 1.0) -> dict:
        """平仓（全部或部分）.

        Parameters
        ----------
        market_id : str
            市场 ID (condition_id).
        pct : float
            平仓比例，0.0 ~ 1.0，默认 1.0 (全部平仓).

        Returns
        -------
        dict
            平仓结果.
        """
        if not 0 < pct <= 1.0:
            raise ValueError(f"pct 必须在 (0, 1.0] 之间，收到 {pct}")

        # 获取持仓
        try:
            positions = self.client.get_positions()
        except PolymarketAPIError as e:
            raise OrderManagerError(f"获取持仓失败: {e}") from e

        # 找到目标市场持仓
        target_positions = [
            p for p in positions
            if p.get("conditionId", "") == market_id
            or p.get("asset", "").startswith(market_id[:10])
        ]

        if not target_positions:
            return {"status": "no_position", "market_id": market_id}

        results = []
        for pos in target_positions:
            token_id = pos.get("asset", "")
            size = float(pos.get("size", 0))
            avg_price = float(pos.get("avgPrice", 0))

            if size <= 0:
                continue

            close_size = size * pct

            # 平仓 = 反向交易
            # 如果当前是 long (买了 yes)，平仓 = sell yes
            # 如果当前持有 token，卖出即可
            try:
                result = self.client.place_order(
                    token_id=token_id,
                    side="SELL",
                    price=max(0.01, round(avg_price * 0.95, 2)),  # 稍微降低以确保成交
                    size=round(close_size, 2),
                    order_type="GTC",
                )
                results.append({
                    "token_id": token_id,
                    "closed_size": close_size,
                    "order": result,
                })
            except (PolymarketAPIError, Exception) as e:
                results.append({
                    "token_id": token_id,
                    "error": str(e),
                })

        self._log_trade({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "close_position",
            "market_id": market_id,
            "pct": pct,
            "results": results,
        })

        return {
            "status": "closed" if results else "no_position",
            "market_id": market_id,
            "details": results,
        }

    # ── 查询 ──────────────────────────────────────────────────

    def get_open_orders(self) -> list[dict]:
        """获取当前所有未成交订单.

        Returns
        -------
        list[dict]
            订单列表.
        """
        try:
            return self.client.get_open_orders()
        except PolymarketAPIError as e:
            raise OrderManagerError(f"获取未成交订单失败: {e}") from e

    def get_trade_log(self, last_n: int = 50) -> list[dict]:
        """读取最近的交易记录.

        Parameters
        ----------
        last_n : int
            返回最近 N 条记录.

        Returns
        -------
        list[dict]
            交易记录列表.
        """
        if not self._log_path.exists():
            return []
        lines = self._log_path.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in lines[-last_n:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _best_price(book: dict, side: str) -> Optional[float]:
        """从 orderbook 提取最优价格."""
        key = "asks" if side == "BUY" else "bids"
        levels = book.get(key, [])
        if not levels:
            return None
        # asks 按价格升序 (最低价最好)，bids 按价格降序 (最高价最好)
        best = levels[0]
        return float(best.get("price", 0))

    def _log_trade(self, record: dict) -> None:
        """追加交易记录到 JSONL 文件."""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
