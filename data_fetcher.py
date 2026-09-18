import asyncio
import tushare as ts


class DataFetcher:
    """封装 tushare 实时行情接口，异步化 + 批量分段"""

    BATCH_SIZE = 50

    async def fetch_quotes(self, codes: list) -> dict:
        if not codes:
            return {}
        results = {}
        for i in range(0, len(codes), self.BATCH_SIZE):
            batch = codes[i:i + self.BATCH_SIZE]
            try:
                part = await asyncio.to_thread(self._fetch_sync, batch)
            except Exception as e:
                # 单批失败不影响其它批次
                print(f"[fetcher] 批次 {batch} 拉取异常: {e}")
                part = {}
            if part:
                results.update(part)
        return results

    @staticmethod
    def _safe_float(value) -> float:
        try:
            if value is None or value == "":
                return 0.0
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    @classmethod
    def _fetch_sync(cls, codes: list) -> dict:
        try:
            df = ts.get_realtime_quotes(codes)
        except Exception as e:
            print(f"[fetcher] tushare 调用失败: {e}")
            return {}

        if df is None or df.empty:
            return {}

        result = {}
        for _, row in df.iterrows():
            try:
                # tushare 不同版本列名可能不同，做兼容
                code = row.get("code")
                if not code:
                    continue
                code = str(code)

                price = cls._safe_float(row.get("price"))
                pre_close = cls._safe_float(
                    row.get("pre_close")
                    or row.get("preclose")
                    or row.get("settlement")
                )
                low = cls._safe_float(row.get("low"))
                high = cls._safe_float(row.get("high"))

                change_pct = (price - pre_close) / pre_close * 100 if pre_close else 0.0
                change_amount = price - pre_close

                name = row.get("name") or code

                result[code] = {
                    "name": name,
                    "price": price,
                    "pre_close": pre_close,
                    "low": low,
                    "high": high,
                    "change_pct": round(change_pct, 2),
                    "change_amount": round(change_amount, 3),
                    "valid": pre_close != 0,
                }
            except (ValueError, TypeError, KeyError) as e:
                print(f"[fetcher] 解析行失败: {e}")
        return result