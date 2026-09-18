import copy
import json
import os
import threading


class ConfigStore:
    """配置持久化，结构: { tab_name: [ {code, name, up_warning, down_warning, buy_price}, ... ]}"""

    DEFAULT_TABS = ("自选", "持仓")
    PROTECTED_TABS = ("自选", "持仓")

    def __init__(self, path: str):
        self.path = path
        # 使用 RLock，避免 save() 在已持锁时再次获取锁导致死锁
        self._lock = threading.RLock()
        self.data: dict = {}
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                return
            except Exception as e:
                print(f"[config] 加载失败: {e}，使用默认配置")
        self.data = {name: [] for name in self.DEFAULT_TABS}

    def save(self):
        with self._lock:
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[config] 保存失败: {e}")

    # ---------- 读 ----------
    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    def has_tab(self, name: str) -> bool:
        with self._lock:
            return name in self.data

    # ---------- 页签 ----------
    def add_tab(self, name: str) -> bool:
        with self._lock:
            if name in self.data:
                return False
            self.data[name] = []
            self.save()
            return True

    def delete_tab(self, name: str) -> bool:
        with self._lock:
            if name not in self.data or name in self.PROTECTED_TABS:
                return False
            del self.data[name]
            self.save()
            return True

    # ---------- 股票 ----------
    def add_stock(self, tab_name: str, stock: dict) -> bool:
        with self._lock:
            if tab_name not in self.data:
                return False
            if any(s["code"] == stock["code"] for s in self.data[tab_name]):
                return False
            self.data[tab_name].append(stock)
            self.save()
            return True

    def update_stock(self, tab_name: str, code: str, updates: dict):
        with self._lock:
            if tab_name not in self.data:
                return None
            for s in self.data[tab_name]:
                if s["code"] == code:
                    s.update(updates)
                    self.save()
                    return copy.deepcopy(s)
        return None

    def delete_stock(self, tab_name: str, code: str) -> bool:
        with self._lock:
            if tab_name not in self.data:
                return False
            arr = self.data[tab_name]
            for i, s in enumerate(arr):
                if s["code"] == code:
                    del arr[i]
                    self.save()
                    return True
        return False