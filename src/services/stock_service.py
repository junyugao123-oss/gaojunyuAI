# -*- coding: utf-8 -*-
"""
===================================
股票数据服务层
===================================

职责：
1. 封装股票数据获取逻辑
2. 提供实时行情和历史数据接口
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)

_HOT_STOCKS_CACHE: Dict[int, tuple[datetime, Dict[str, Any]]] = {}
_HOT_STOCKS_CACHE_SECONDS = 45
_HOT_STOCKS_FETCH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hot-stocks")
_HOT_STOCKS_FAST_FALLBACK: List[Dict[str, Any]] = [
    {"rank": 1, "code": "002129.SZ", "name": "TCL中环", "change_pct": 0.0, "reason": "半导体材料与新能源链关注度较高"},
    {"rank": 2, "code": "603650.SH", "name": "彤程新材", "change_pct": 0.0, "reason": "光刻胶与电子材料方向关注度较高"},
    {"rank": 3, "code": "688234.SH", "name": "天岳先进", "change_pct": 0.0, "reason": "碳化硅半导体材料关注度较高"},
    {"rank": 4, "code": "300750.SZ", "name": "宁德时代", "change_pct": 0.0, "reason": "新能源龙头关注度稳定"},
    {"rank": 5, "code": "002594.SZ", "name": "比亚迪", "change_pct": 0.0, "reason": "智能电动车与电池链关注度稳定"},
    {"rank": 6, "code": "600519.SH", "name": "贵州茅台", "change_pct": 0.0, "reason": "消费龙头关注度稳定"},
    {"rank": 7, "code": "HK6651", "name": "五一视界", "change_pct": 0.0, "reason": "物理AI与数字孪生方向关注度较高"},
    {"rank": 8, "code": "688981.SH", "name": "中芯国际", "change_pct": 0.0, "reason": "国产半导体制造关注度较高"},
    {"rank": 9, "code": "0700.HK", "name": "腾讯控股", "change_pct": 0.0, "reason": "港股互联网龙头关注度稳定"},
    {"rank": 10, "code": "9988.HK", "name": "阿里巴巴-W", "change_pct": 0.0, "reason": "港股互联网平台关注度稳定"},
]


def _hot_stocks_timeout_seconds() -> float:
    try:
        return max(0.8, min(float(os.getenv("HOT_STOCKS_TIMEOUT_SECONDS", "3.5")), 8.0))
    except (TypeError, ValueError):
        return 3.5


class StockService:
    """
    股票数据服务
    
    封装股票数据获取的业务逻辑
    """
    
    def __init__(self):
        """初始化股票数据服务"""
        self.repo = StockRepository()
    
    def get_realtime_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取股票实时行情
        
        Args:
            stock_code: 股票代码
            
        Returns:
            实时行情数据字典
        """
        try:
            # 调用数据获取器获取实时行情
            from data_provider.base import DataFetcherManager
            
            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(stock_code)
            
            if quote is None:
                logger.warning(f"获取 {stock_code} 实时行情失败")
                return None
            
            # UnifiedRealtimeQuote 是 dataclass，使用 getattr 安全访问字段
            # 字段映射: UnifiedRealtimeQuote -> API 响应
            # - code -> stock_code
            # - name -> stock_name
            # - price -> current_price
            # - change_amount -> change
            # - change_pct -> change_percent
            # - open_price -> open
            # - high -> high
            # - low -> low
            # - pre_close -> prev_close
            # - volume -> volume
            # - amount -> amount
            return {
                "stock_code": getattr(quote, "code", stock_code),
                "stock_name": getattr(quote, "name", None),
                "current_price": getattr(quote, "price", 0.0) or 0.0,
                "change": getattr(quote, "change_amount", None),
                "change_percent": getattr(quote, "change_pct", None),
                "open": getattr(quote, "open_price", None),
                "high": getattr(quote, "high", None),
                "low": getattr(quote, "low", None),
                "prev_close": getattr(quote, "pre_close", None),
                "volume": getattr(quote, "volume", None),
                "amount": getattr(quote, "amount", None),
                "update_time": datetime.now().isoformat(),
            }
            
        except ImportError:
            logger.warning("DataFetcherManager 未找到，使用占位数据")
            return self._get_placeholder_quote(stock_code)
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}", exc_info=True)
            return None

    def get_hot_stocks(self, limit: int = 10, timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
        """
        获取市场热门股，并用排名、涨跌幅、成交额生成稳定的热度分。

        Args:
            limit: 返回数量

        Returns:
            热门股列表
        """
        cache_limit = max(1, min(limit, 30))
        now = datetime.now()
        cached = _HOT_STOCKS_CACHE.get(cache_limit)
        if cached and (now - cached[0]).total_seconds() <= _HOT_STOCKS_CACHE_SECONDS:
            payload = cached[1]
            return {
                **payload,
                "stocks": [dict(item) for item in payload.get("stocks", [])],
            }

        rows = self._fetch_hot_rows_with_timeout(cache_limit, timeout_seconds=timeout_seconds)
        stocks = self._build_hot_stock_items(rows, cache_limit)
        if not stocks:
            logger.warning("[人气股] 实时热榜暂不可用，使用快速热门候选兜底")
            stocks = self._build_hot_stock_items(self._fallback_hot_rows(cache_limit), cache_limit)

        stocks.sort(key=lambda item: (-(item.get("hot_score") or 0), item.get("rank") or 999))
        result = {
            "stocks": stocks[:cache_limit],
            "generated_at": now.isoformat(),
        }
        if result["stocks"]:
            _HOT_STOCKS_CACHE[cache_limit] = (now, {
                **result,
                "stocks": [dict(item) for item in result["stocks"]],
            })
        return result

    def _fetch_hot_rows_with_timeout(self, limit: int, timeout_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        timeout_seconds = (
            max(0.4, min(float(timeout_seconds), 8.0))
            if timeout_seconds is not None
            else _hot_stocks_timeout_seconds()
        )
        future = _HOT_STOCKS_FETCH_EXECUTOR.submit(self._fetch_remote_hot_rows, limit)
        future.add_done_callback(lambda done: self._cache_completed_hot_rows(done, limit))
        try:
            return future.result(timeout=timeout_seconds) or []
        except FuturesTimeoutError:
            logger.warning("[人气股] 热榜数据源 %.1fs 内未返回，先使用快速候选", timeout_seconds)
            return []
        except Exception as e:
            logger.warning(f"获取市场热门股失败: {e}", exc_info=True)
            return []

    @staticmethod
    def _fetch_remote_hot_rows(limit: int) -> List[Dict[str, Any]]:
        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager()
        return manager.get_hot_stocks(limit) or []

    def _cache_completed_hot_rows(self, future: Any, limit: int) -> None:
        try:
            rows = future.result()
        except Exception:
            return
        stocks = self._build_hot_stock_items(rows, limit)
        if not stocks:
            return
        now = datetime.now()
        _HOT_STOCKS_CACHE[limit] = (now, {
            "stocks": [dict(item) for item in stocks[:limit]],
            "generated_at": now.isoformat(),
        })

    def _fallback_hot_rows(self, limit: int) -> List[Dict[str, Any]]:
        if not _HOT_STOCKS_FAST_FALLBACK:
            return []
        day_offset = datetime.now().timetuple().tm_yday % len(_HOT_STOCKS_FAST_FALLBACK)
        rotated = _HOT_STOCKS_FAST_FALLBACK[day_offset:] + _HOT_STOCKS_FAST_FALLBACK[:day_offset]
        return [
            {
                **row,
                "rank": index + 1,
            }
            for index, row in enumerate(rotated[:limit])
        ]

    def _build_hot_stock_items(self, rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        try:
            from data_provider.base import normalize_stock_code
        except Exception:
            normalize_stock_code = None

        stocks: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for idx, row in enumerate(rows or [], 1):
            raw_code = str(row.get("code") or row.get("stock_code") or "").strip()
            name = str(row.get("name") or row.get("stock_name") or "").strip()
            if not raw_code or not name:
                continue
            if normalize_stock_code:
                try:
                    code = normalize_stock_code(raw_code)
                except Exception:
                    code = raw_code
            else:
                code = raw_code
            code_key = code.upper()
            if code_key in seen:
                continue
            seen.add(code_key)

            rank = row.get("rank") if row.get("rank") is not None else idx
            try:
                rank_value = int(rank)
            except (TypeError, ValueError):
                rank_value = idx
            change_pct = self._safe_float(row.get("change_pct") or row.get("change_percent"))
            amount = self._safe_float(row.get("amount"))
            amount_score = min(16.0, max(0.0, (len(str(int(amount))) - 7) * 2.2)) if amount else 0.0
            change_score = max(-8.0, min(18.0, (change_pct or 0.0) * 2.1))
            rank_score = max(0.0, 100.0 - (rank_value - 1) * 4.5)
            hot_score = max(0.0, min(100.0, rank_score + change_score + amount_score))

            reason_parts = [str(row.get("reason") or "热度排名靠前")]
            if change_pct is not None:
                reason_parts.append("涨幅活跃" if change_pct > 0 else "关注度活跃")
            if amount:
                reason_parts.append("成交活跃")

            stocks.append({
                "rank": rank_value,
                "code": code,
                "name": name,
                "price": self._safe_float(row.get("price")),
                "change_percent": change_pct,
                "hot_score": round(hot_score, 1),
                "reason": " · ".join(reason_parts[:3]),
            })
            if len(stocks) >= limit:
                break

        return stocks
    
    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30
    ) -> Dict[str, Any]:
        """
        获取股票历史行情
        
        Args:
            stock_code: 股票代码
            period: K 线周期 (daily/weekly/monthly)
            days: 获取天数
            
        Returns:
            历史行情数据字典
            
        Raises:
            ValueError: 当 period 不是 daily 时抛出（weekly/monthly 暂未实现）
        """
        # 验证 period 参数，只支持 daily
        if period != "daily":
            raise ValueError(
                f"暂不支持 '{period}' 周期，目前仅支持 'daily'。"
                "weekly/monthly 聚合功能将在后续版本实现。"
            )
        
        try:
            # 调用数据获取器获取历史数据
            from data_provider.base import DataFetcherManager
            
            manager = DataFetcherManager()
            df, source = manager.get_daily_data(stock_code, days=days)
            
            if df is None or df.empty:
                logger.warning(f"获取 {stock_code} 历史数据失败")
                return {"stock_code": stock_code, "period": period, "data": []}
            
            # 获取股票名称
            stock_name = manager.get_stock_name(stock_code)
            
            # 转换为响应格式
            data = []
            for _, row in df.iterrows():
                date_val = row.get("date")
                if hasattr(date_val, "strftime"):
                    date_str = date_val.strftime("%Y-%m-%d")
                else:
                    date_str = str(date_val)
                
                data.append({
                    "date": date_str,
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)) if row.get("volume") else None,
                    "amount": float(row.get("amount", 0)) if row.get("amount") else None,
                    "change_percent": float(row.get("pct_chg", 0)) if row.get("pct_chg") else None,
                })
            
            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "period": period,
                "data": data,
            }
            
        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return {"stock_code": stock_code, "period": period, "data": []}
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return {"stock_code": stock_code, "period": period, "data": []}

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
    
    def _get_placeholder_quote(self, stock_code: str) -> Dict[str, Any]:
        """
        获取占位行情数据（用于测试）
        
        Args:
            stock_code: 股票代码
            
        Returns:
            占位行情数据
        """
        return {
            "stock_code": stock_code,
            "stock_name": f"股票{stock_code}",
            "current_price": 0.0,
            "change": None,
            "change_percent": None,
            "open": None,
            "high": None,
            "low": None,
            "prev_close": None,
            "volume": None,
            "amount": None,
            "update_time": datetime.now().isoformat(),
        }
