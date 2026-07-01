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

import requests

from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)

_HOT_STOCKS_CACHE: Dict[int, tuple[datetime, Dict[str, Any]]] = {}
_HOT_STOCKS_CACHE_SECONDS = 45
_HOT_STOCKS_FETCH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hot-stocks")
_HOT_STOCKS_REALTIME_CANDIDATES: List[Dict[str, str]] = [
    {"code": "002129.SZ", "name": "TCL中环", "reason": "半导体材料与新能源链候选"},
    {"code": "603650.SH", "name": "彤程新材", "reason": "光刻胶与电子材料候选"},
    {"code": "688234.SH", "name": "天岳先进", "reason": "碳化硅半导体材料候选"},
    {"code": "002407.SZ", "name": "多氟多", "reason": "氟化工与新能源材料候选"},
    {"code": "002121.SZ", "name": "科陆电子", "reason": "储能与电力设备候选"},
    {"code": "002594.SZ", "name": "比亚迪", "reason": "智能电动车与电池链候选"},
    {"code": "300750.SZ", "name": "宁德时代", "reason": "动力电池链候选"},
    {"code": "300274.SZ", "name": "阳光电源", "reason": "新能源逆变器候选"},
    {"code": "601138.SH", "name": "工业富联", "reason": "AI服务器与算力链候选"},
    {"code": "300502.SZ", "name": "新易盛", "reason": "高速光模块候选"},
    {"code": "300308.SZ", "name": "中际旭创", "reason": "高速光模块候选"},
    {"code": "002463.SZ", "name": "沪电股份", "reason": "AI服务器PCB候选"},
    {"code": "300476.SZ", "name": "胜宏科技", "reason": "AI服务器PCB候选"},
    {"code": "688256.SH", "name": "寒武纪", "reason": "AI芯片候选"},
    {"code": "688981.SH", "name": "中芯国际", "reason": "国产半导体制造候选"},
    {"code": "002371.SZ", "name": "北方华创", "reason": "半导体设备候选"},
    {"code": "688012.SH", "name": "中微公司", "reason": "半导体设备候选"},
    {"code": "002156.SZ", "name": "通富微电", "reason": "先进封装候选"},
    {"code": "300223.SZ", "name": "北京君正", "reason": "芯片设计候选"},
    {"code": "300666.SZ", "name": "江丰电子", "reason": "半导体材料候选"},
    {"code": "300124.SZ", "name": "汇川技术", "reason": "工业自动化候选"},
    {"code": "002050.SZ", "name": "三花智控", "reason": "机器人与热管理候选"},
    {"code": "002472.SZ", "name": "双环传动", "reason": "机器人传动候选"},
    {"code": "002896.SZ", "name": "中大力德", "reason": "机器人减速器候选"},
    {"code": "300024.SZ", "name": "机器人", "reason": "机器人整机候选"},
    {"code": "300607.SZ", "name": "拓斯达", "reason": "工业机器人候选"},
    {"code": "300496.SZ", "name": "中科创达", "reason": "智能汽车与端侧AI候选"},
    {"code": "300339.SZ", "name": "润和软件", "reason": "鸿蒙与国产软件候选"},
    {"code": "002230.SZ", "name": "科大讯飞", "reason": "AI应用候选"},
    {"code": "300418.SZ", "name": "昆仑万维", "reason": "AI应用候选"},
    {"code": "300115.SZ", "name": "长盈精密", "reason": "消费电子与机器人候选"},
    {"code": "002475.SZ", "name": "立讯精密", "reason": "消费电子与AI硬件候选"},
    {"code": "000977.SZ", "name": "浪潮信息", "reason": "AI服务器候选"},
    {"code": "603019.SH", "name": "中科曙光", "reason": "算力基础设施候选"},
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

        rows = self._fetch_hot_rows_with_timeout(cache_limit * 2, timeout_seconds=timeout_seconds)
        stocks = self._build_hot_stock_items(rows, cache_limit)
        if not stocks:
            fallback_rows = self._fetch_realtime_candidate_hot_rows(cache_limit * 3)
            stocks = self._build_hot_stock_items(fallback_rows, cache_limit)

        if not stocks:
            logger.warning("[人气股] 实时热榜与快速实时候选均不可用，返回空列表以保持首页普通搜索占位")
            return {
                "stocks": [],
                "generated_at": now.isoformat(),
            }

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
        if not _HOT_STOCKS_REALTIME_CANDIDATES:
            return []
        day_offset = datetime.now().timetuple().tm_yday % len(_HOT_STOCKS_REALTIME_CANDIDATES)
        rotated = _HOT_STOCKS_REALTIME_CANDIDATES[day_offset:] + _HOT_STOCKS_REALTIME_CANDIDATES[:day_offset]
        return [
            {
                **row,
                "rank": index + 1,
            }
            for index, row in enumerate(rotated[:limit])
        ]

    @staticmethod
    def _tencent_symbol(code: str) -> Optional[str]:
        compact = (code or "").strip().upper()
        if not compact:
            return None
        if compact.endswith(".SH"):
            return f"sh{compact[:-3]}"
        if compact.endswith(".SZ"):
            return f"sz{compact[:-3]}"
        digits = "".join(ch for ch in compact if ch.isdigit())
        if len(digits) != 6:
            return None
        if digits.startswith(("6", "9")):
            return f"sh{digits}"
        if digits.startswith(("0", "2", "3")):
            return f"sz{digits}"
        return None

    def _fetch_realtime_candidate_hot_rows(self, limit: int) -> List[Dict[str, Any]]:
        """Use Tencent batch quotes to build a fast intraday-gainer fallback list."""
        candidates = self._fallback_hot_rows(max(limit, len(_HOT_STOCKS_REALTIME_CANDIDATES)))
        symbol_to_candidate: Dict[str, Dict[str, Any]] = {}
        for candidate in candidates:
            symbol = self._tencent_symbol(str(candidate.get("code") or ""))
            if symbol:
                symbol_to_candidate[symbol] = candidate
        if not symbol_to_candidate:
            return []

        try:
            response = requests.get(
                "http://qt.gtimg.cn/q=" + ",".join(symbol_to_candidate.keys()),
                headers={
                    "Referer": "http://finance.qq.com",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=5,
            )
            response.encoding = "gbk"
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[人气股] 腾讯批量实时候选失败: %s", exc)
            return []

        rows: List[Dict[str, Any]] = []
        for line in response.text.split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            symbol = line.split("=", 1)[0].replace("v_", "").strip()
            candidate = symbol_to_candidate.get(symbol)
            if not candidate:
                continue
            data_start = line.find('"')
            data_end = line.rfind('"')
            if data_start == -1 or data_end <= data_start:
                continue
            fields = line[data_start + 1:data_end].split("~")
            if len(fields) < 33:
                continue
            change_pct = self._safe_float(fields[32])
            if change_pct is None or change_pct < 1.0:
                continue
            amount = self._safe_float(fields[37]) if len(fields) > 37 else None
            amount_yuan = amount * 10000 if amount is not None else None
            rows.append({
                "rank": 999,
                "code": candidate.get("code"),
                "name": fields[1] or candidate.get("name"),
                "price": self._safe_float(fields[3]),
                "change_pct": change_pct,
                "amount": amount_yuan,
                "reason": candidate.get("reason") or "实时涨幅候选",
            })

        rows.sort(key=lambda item: (-(item.get("change_pct") or 0), -(item.get("amount") or 0)))
        for index, row in enumerate(rows, 1):
            row["rank"] = index
            row["reason"] = f"实时涨幅候选 · {row.get('reason')}"
        return rows[:limit]

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
            raw_change_pct = row.get("change_pct")
            if raw_change_pct is None:
                raw_change_pct = row.get("change_percent")
            change_pct = self._safe_float(raw_change_pct)
            if change_pct is None or change_pct < 1.0:
                continue
            amount = self._safe_float(row.get("amount"))
            amount_score = min(12.0, max(0.0, (len(str(int(amount))) - 7) * 1.8)) if amount else 0.0
            change_value = change_pct if change_pct is not None else 0.0
            change_score = max(-35.0, min(68.0, change_value * 7.5))
            rank_score = max(0.0, 52.0 - (rank_value - 1) * 2.4)
            positive_heat_bonus = 8.0 if change_pct is not None and change_pct >= 2.0 else 0.0
            hot_score = max(0.0, min(100.0, rank_score + change_score + amount_score + positive_heat_bonus))

            reason_parts = [str(row.get("reason") or "热度排名靠前")]
            if change_pct is not None:
                reason_parts.append(f"涨幅{change_pct:+.2f}%" if change_pct > 0 else "关注度活跃")
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
