class AlertManager:
    """去重式价格预警：只有从未触发状态进入触发状态时才报警"""

    def __init__(self):
        self._state: dict = {}   # key = f"{tab}_{code}" -> {"up": bool, "down": bool}

    def check(self, tab_name: str, stock: dict, quote: dict) -> list:
        alerts = []
        code = stock["code"]
        name = stock.get("name") or quote.get("name") or code
        price = quote.get("price", 0.0)
        if not price:
            return alerts

        key = f"{tab_name}_{code}"
        st = self._state.setdefault(key, {"up": False, "down": False})

        up = stock.get("up_warning")
        if up is not None:
            if price >= up:
                if not st["up"]:
                    st["up"] = True
                    alerts.append({
                        "type": "up",
                        "color": "red",
                        "title": "⚠️ 股票预警提示 ⚠️",
                        "message": f"{name}({code})\n当前价格: {price:.2f}元\n预警涨价: {up:.2f}元",
                    })
            else:
                st["up"] = False

        down = stock.get("down_warning")
        if down is not None:
            if price <= down:
                if not st["down"]:
                    st["down"] = True
                    alerts.append({
                        "type": "down",
                        "color": "green",
                        "title": "⚠️ 股票预警提示 ⚠️",
                        "message": f"{name}({code})\n当前价格: {price:.2f}元\n预警跌价: {down:.2f}元",
                    })
            else:
                st["down"] = False

        return alerts

    def clear(self, tab_name: str, code: str):
        self._state.pop(f"{tab_name}_{code}", None)

    def clear_tab(self, tab_name: str):
        """页签被删除时清理该页签下所有预警状态"""
        prefix = f"{tab_name}_"
        for key in [k for k in self._state if k.startswith(prefix)]:
            self._state.pop(key, None)

    def prune(self, valid_keys: set):
        """清理不再存在的股票对应状态，防止 _state 无限增长"""
        for key in [k for k in self._state if k not in valid_keys]:
            self._state.pop(key, None)