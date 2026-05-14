#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票实时监控系统
基于 tushare + tkinter 实现
功能：添加/删除股票、价格排序、价格预警弹窗、JSON 配置文件持久化
"""
import json
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import tushare as ts

# ============================================================
# 配置文件路径
# ============================================================
CONFIG_FILE = "stock_config.json"
DEFAULT_REFRESH_INTERVAL = 1  # 默认刷新间隔（秒）
IS_OWN_CODE = False
SHOW_NOTICE = {}

# ============================================================
# 配置管理模块
# ============================================================
def load_config():
    """从 JSON 文件加载股票配置"""
    if not os.path.exists(CONFIG_FILE):
        # 默认示例配置
        default_config = {
            "stocks": {
                "000001": {"alert_price_up": None},
                "002115": {"alert_price_up": 15.50, "alert_price_down":14.82},
            },
            "refresh_interval": DEFAULT_REFRESH_INTERVAL,
        }
        save_config(default_config)
        return default_config
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"stocks": {}, "refresh_interval": DEFAULT_REFRESH_INTERVAL}


def save_config(config):
    """将股票配置保存到 JSON 文件"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"保存配置文件失败: {e}")


# ============================================================
# 数据获取模块
# ============================================================
def restart_program():
    """重启当前程序"""
    print("程序正在重启...")
    python = sys.executable  # 获取当前 Python 解释器路径
    os.execv(python, [python] + sys.argv)

def fixed_map(style, option):
    """过滤掉干扰颜色设置的样式规则"""
    return [elm for elm in style.map('Treeview', query_opt=option)
            if elm[:2] != ('!disabled', '!selected')]

def fetch_realtime_quotes(codes):
    """
    批量获取实时行情数据
    参数 codes: 股票代码列表（如 ['000001', '600519']）
    返回: DataFrame 或 None
    """
    if not codes:
        return None
    try:
        df = ts.get_realtime_quotes(codes)
        return df
    except Exception as e:
        print(f"获取行情数据失败: {e}")
        time.sleep(1)
        restart_program()
        sys.exit(0)
        return None

def parse_quote(df):
    """
    解析实时行情 DataFrame，提取关键字段
    返回字段说明（来自 get_realtime_quotes）：
        name: 股票名称
        price: 当前价格
        pre_close: 昨日收盘价
        open: 今日开盘价
        high: 今日最高价
        low: 今日最低价
        volume: 成交量
        amount: 成交金额
        time: 更新时间
    """
    if df is None or df.empty:
        return {}
    result = {}
    for _, row in df.iterrows():
        code = row.get("code", "")
        try:
            price = float(row.get("price", 0))
        except (ValueError, TypeError):
            price = 0.0
        try:
            pre_close = float(row.get("pre_close", 0))
        except (ValueError, TypeError):
            pre_close = 0.0
        change_pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0.0
        result[code] = {
            "name": row.get("name", ""),
            "price": price,
            "pre_close": pre_close,
            "open": row.get("open", ""),
            "high": row.get("high", ""),
            "low": row.get("low", ""),
            "volume": row.get("volume", ""),
            "time": row.get("time", ""),
            "change_pct": round(change_pct, 2),
        }
    return result


# ============================================================
# GUI 主界面
# ============================================================
class StockMonitorApp:
    """股票实时监控主应用"""

    def __init__(self, root):
        self.root = root
        self.root.title("股票实时监控系统")
        self.root.geometry("900x600")
        self.root.minsize(800, 500)
        # style = ttk.Style()
        # style.theme_use('vista')
        # available_themes = style.theme_names()
        # print(f"所有可用主题: {available_themes}")
        # 加载配置
        self.config = load_config()
        self.stock_data = {}  # 当前行情数据缓存

        style = ttk.Style()
        style.theme_use('vista')
        style.map('Treeview',
          foreground=fixed_map(style, 'foreground'),
          background=fixed_map(style, 'background'))

        # 创建界面
        self._build_ui()

        # 首次刷新数据
        self.refresh_data()

        # 启动定时刷新
        self._schedule_refresh()

    # ---------- 界面构建 ----------
    def _build_ui(self):
        """构建主界面布局"""
        # 顶部：添加/删除股票区域
        top_frame = ttk.LabelFrame(self.root, text="股票管理", padding=10)
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(top_frame, text="股票代码:").pack(side=tk.LEFT, padx=(0, 5))
        self.code_entry = ttk.Entry(top_frame, width=12)
        self.code_entry.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Label(top_frame, text="预警涨价格:").pack(side=tk.LEFT, padx=(0, 5))
        self.alert_entry_up = ttk.Entry(top_frame, width=6)
        self.alert_entry_up.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Label(top_frame, text="预警跌价格:").pack(side=tk.LEFT, padx=(0, 5))
        self.alert_entry_down = ttk.Entry(top_frame, width=6)
        self.alert_entry_down.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Radiobutton(top_frame, variable="", value="", text="持仓", command=self.setOwnCode).pack(side=tk.LEFT, padx=3)

        ttk.Button(top_frame, text="添加", command=self.add_stock).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="删除选中", command=self.delete_stock).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="手动刷新", command=self.refresh_data).pack(side=tk.LEFT, padx=5)

        # 刷新间隔设置
        ttk.Label(top_frame, text="刷新间隔(秒):").pack(side=tk.LEFT, padx=(0, 0))
        self.interval_var = tk.StringVar(value=str(self.config.get("refresh_interval", DEFAULT_REFRESH_INTERVAL)))
        interval_spin = ttk.Spinbox(
            top_frame, from_=1, to=60, textvariable=self.interval_var, width=5
        )
        interval_spin.pack(side=tk.LEFT, padx=0)
        interval_spin.bind("<FocusOut>", self._on_interval_change)

        # 透明度设置滑块
        slider = tk.Scale(
                top_frame,
                from_=0.1,        # 最小值
                to=1,         # 最大值
                orient=tk.HORIZONTAL,  # 方向
                length=100,     # 滑块长度
                width=10,       # 滑块宽度
                sliderlength=20, # 滑块手柄长度
                resolution=0.1,   # 步长（精度）
                showvalue=0, #不显示文本
                command=self.set_ui_alpha  # 值改变时的回调函数
            )
        slider.set(0.2)
        self.root.wm_attributes("-alpha", 0.2)
        slider.place(x=752, y=22)

        # 中间：排序控制栏
        sort_frame = ttk.Frame(self.root)
        sort_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(sort_frame, text="排序方式:").pack(side=tk.LEFT, padx=(0, 5))
        self.sort_var = tk.StringVar(value="price_desc")
        ttk.Radiobutton(sort_frame, text="价格↓", variable=self.sort_var, value="price_desc", command=self._sort_and_display).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(sort_frame, text="价格↑", variable=self.sort_var, value="price_asc", command=self._sort_and_display).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(sort_frame, text="涨幅↓", variable=self.sort_var, value="change_desc", command=self._sort_and_display).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(sort_frame, text="涨幅↑", variable=self.sort_var, value="change_asc", command=self._sort_and_display).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(sort_frame, text="自选", variable=self.sort_var, value="self_select", command=self._sort_and_display).pack(side=tk.LEFT, padx=3)
        ttk.Radiobutton(sort_frame, text="持仓", variable=self.sort_var, value="self_own", command=self._sort_and_display).pack(side=tk.LEFT, padx=3)
        # 状态显示
        self.status_var = tk.StringVar(value="等待数据...")
        ttk.Label(sort_frame, textvariable=self.status_var, foreground="gray").pack(side=tk.RIGHT)

        # 下方：股票行情列表（带滚动条）
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        # 列定义：代码 | 名称 | 当前价 | 涨跌幅 | 昨收 | 开盘 | 最高 | 最低 | 成交量 | 预警价
        columns = ("code", "name", "price", "change_pct", "pre_close", "open", "high", "low", "volume", "alert_price_up", "alert_price_down")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=20)

        # 设置列标题
        col_config = [
            ("code", "代码", 70),
            ("name", "名称", 80),
            ("price", "当前价", 80),
            ("change_pct", "涨跌幅%", 80),
            ("pre_close", "昨收", 70),
            ("open", "开盘", 70),
            ("high", "最高", 70),
            ("low", "最低", 70),
            ("volume", "成交量", 90),
            ("alert_price_up", "预警涨价", 80),
            ("alert_price_down", "预警跌价", 80),
        ]
        for col_id, col_text, col_width in col_config:
            self.tree.heading(col_id, text=col_text, anchor=tk.CENTER)
            self.tree.column(col_id, width=col_width, anchor=tk.CENTER)

        # 滚动条
        sb_y = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        sb_x = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)

        # 网格布局
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        # 基础黑色（默认颜色）
        self.tree.tag_configure('positive', foreground='red', background='#ffffff', font=('微软雅黑', 12, 'bold'))
        self.tree.tag_configure('negative', foreground='green', background='#ffffff', font=('微软雅黑', 12))
        self.tree.tag_configure('zero', foreground='black', background='#ffffff', font=('微软雅黑', 12))

        # 绑定双击修改预警价格
        self.tree.bind("<Double-1>", self._on_double_click)
        # self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def set_ui_alpha(self, value):
        self.root.wm_attributes("-alpha", value)

    # ---------- 股票管理 ----------
    def add_stock(self):
        """添加股票"""
        code = self.code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入股票代码")
            return
        # 校验股票代码格式（6位数字）
        stock = ts.get_realtime_quotes(code)
        if stock is None:
            messagebox.showwarning("提示", "代码{0}错误".format(code))
            return

        if code in self.config.get("stocks", {}):
            messagebox.showinfo("提示", f"股票 {code} 已在列表中")
            return

        # 获取预警价格（可选）
        alert_price_up = None
        alert_price_down = None
        alert_str_up = self.alert_entry_up.get().strip()
        alert_str_down = self.alert_entry_down.get().strip()
        if alert_str_up:
            try:
                alert_price_up = float(alert_str_up)
            except ValueError:
                messagebox.showwarning("提示", "预警涨价格格式不正确")
                return
        if alert_str_down:
            try:
                alert_price_down = float(alert_str_down)
            except ValueError:
                messagebox.showwarning("提示", "预警跌价格格式不正确")
                return

        # 更新配置
        if "stocks" not in self.config:
            self.config["stocks"] = {}
        self.config["stocks"][code] = {"alert_price_up": alert_price_up, "alert_price_down": alert_price_down}
        global IS_OWN_CODE
        if IS_OWN_CODE:
            if "selfown" not in self.config:
                self.config["selfown"] = []
            self.config["selfown"].append(code)
        save_config(self.config)

        # 清空输入框
        self.code_entry.delete(0, tk.END)
        self.alert_entry_up.delete(0, tk.END)
        self.alert_entry_down.delete(0, tk.END)
        IS_OWN_CODE = False
        # 立即刷新显示
        self.refresh_data()
        self.status_var.set(f"已添加股票: {code}")


    def delete_stock(self):
        """删除选中的股票"""
        code = self.code_entry.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入股票代码")
            return
        if code not in self.config.get("stocks", {}):
            messagebox.showinfo("提示", f"股票 {code} 不在列表中")
            return
        del self.config["stocks"][code]
        save_config(self.config)

        # 清空输入框
        self.code_entry.delete(0, tk.END)
        self.alert_entry_up.delete(0, tk.END)
        self.alert_entry_down.delete(0, tk.END)

        # 立即刷新显示
        self.refresh_data()
        self.status_var.set("已删该股票")

    # ---------- 数据刷新 ----------
    def refresh_data(self):
        """刷新实时行情数据"""
        codes = list(self.config.get("stocks", {}).keys())
        if not codes:
            self.status_var.set("暂无股票，请添加")
            return

        # 在后台线程中获取数据，避免界面卡顿
        def _fetch():
            df = fetch_realtime_quotes(codes)
            self.stock_data = parse_quote(df)
            # 回到主线程更新界面
            self.root.after(0, self._sort_and_display)
            self.root.after(0, self._check_alerts)
        # 线程执行完 _fetch 后，自动清理，不需要 join() 或其他操作
        threading.Thread(target=_fetch, daemon=True).start()
        self.status_var.set("正在刷新数据...")

    def setOwnCode(self):
        global IS_OWN_CODE
        IS_OWN_CODE = True

    # ---------- 排序与显示 ----------
    def _sort_and_display(self):
        """按当前排序方式显示数据"""
        # 清空现有数据
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.stock_data:
            self.status_var.set("暂无数据")
            return

        # 构建排序列表
        items = []
        stocks_config = self.config.get("stocks", {})
        for code, info in self.stock_data.items():
            alert_price_up = stocks_config.get(code, {}).get("alert_price_up")
            alert_price_down = stocks_config.get(code, {}).get("alert_price_down")
            cur_price = info.get("price", 0)
            pre_close_price = info.get("pre_close", 0)
            open_price = info.get("open", 0)
            high_price = info.get("high", "")
            low_price = info.get("low", "")
            volume_price = info.get("volume", "")
            if float(pre_close_price) > 1000:
                if float(pre_close_price) > 1000000:
                    pre_close_price = "{0}百万".format(int(float(pre_close_price) / 1000000))
                else:
                    pre_close_price = "{0}千".format(int(float(pre_close_price) / 1000))
            if float(open_price) > 1000:
                if float(open_price) > 1000000:
                    open_price = "{0}百万".format(int(float(open_price) / 1000000))
                else:
                    open_price = "{0}千".format(int(float(open_price) / 1000))
            if float(high_price) > 1000:
                if float(high_price) > 1000000:
                    high_price = "{0}百万".format(int(float(high_price) / 1000000))
                else:
                    high_price = "{0}千".format(int(float(high_price) / 1000))
            if float(low_price) > 1000:
                if float(low_price) > 1000000:
                    low_price = "{0}百万".format(int(float(low_price) / 1000000))
                else:
                    low_price = "{0}千".format(int(float(low_price) / 1000))
            if float(volume_price) > 1000:
                if float(volume_price) > 1000000:
                    volume_price = "{0}百万".format(int(float(volume_price) / 1000000))
                else:
                    volume_price = "{0}千".format(int(float(volume_price) / 1000))
            items.append((
                code,
                info.get("name", ""),
                cur_price,
                info.get("change_pct", 0),
                pre_close_price,
                open_price,
                high_price,
                low_price,
                volume_price,
                alert_price_up if alert_price_up is not None else "未设",
                alert_price_down if alert_price_down is not None else "未设",
            ))

        # 排序
        own_code = []
        is_own = False
        sort_mode = self.sort_var.get()
        if sort_mode == "price_desc":
            items.sort(key=lambda x: x[2], reverse=True)
        elif sort_mode == "price_asc":
            items.sort(key=lambda x: x[2])
        elif sort_mode == "change_desc":
            items.sort(key=lambda x: x[3], reverse=True)
        elif sort_mode == "change_asc":
            items.sort(key=lambda x: x[3])
        elif sort_mode == "self_select":
            pass
        elif sort_mode == "self_own":
            own_code = self.config.get("selfown", [])
            is_own = True

        # 插入 Treeview
        for item in items:
            code = item[0]
            if (is_own == False) or (code in own_code):
                change_pct = item[3]
                if change_pct > 0:
                    tag = 'positive'
                elif change_pct < 0:
                    tag = 'negative'
                else:
                    tag = 'zero'
                self.tree.insert("", tk.END, values=item, tags=tag)

        now = datetime.now().strftime("%H:%M:%S")
        self.status_var.set(f"数据已刷新 | 时间: {now} | 共 {len(items)} 只股票")

    # ---------- 价格预警 ----------
    def _check_alerts(self):
        """检查价格是否超过预警阈值"""
        stocks_config = self.config.get("stocks", {})
        for code, info in self.stock_data.items():
            current_price = info.get("price", 0)
            alert_price_up = stocks_config.get(code, {}).get("alert_price_up")
            alert_price_down = stocks_config.get(code, {}).get("alert_price_down")
            if alert_price_up is not None:
                if current_price >= alert_price_up:
                    self._show_alert(code, info.get("name", ""), current_price, alert_price_up)

            if alert_price_down is not None:
                if current_price <= alert_price_down:
                    self._show_alert(code, info.get("name", ""), current_price, alert_price_down)

    def _show_alert(self, code, name, price, alert_price):
        codeStr = str(code)
        global SHOW_NOTICE
        if codeStr not in SHOW_NOTICE:
            SHOW_NOTICE[codeStr] = []

        if alert_price in SHOW_NOTICE[codeStr]:
            return

        SHOW_NOTICE[codeStr].append(alert_price)

        """显示价格预警弹窗"""
        alert_win = tk.Toplevel(self.root)
        alert_win.title("⚠️ 价格预警")
        alert_win.geometry("350x180")
        alert_win.update_idletasks()           # 刷新，让窗口尺寸生效
        # 获取主窗口的位置和尺寸
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()

        # 获取子窗口的尺寸
        top_w = alert_win.winfo_width()
        top_h = alert_win.winfo_height()

        # 计算子窗口左上角坐标
        x = root_x + (root_w - top_w) // 2
        y = root_y + (root_h - top_h) // 2
        # 设置子窗口位置
        alert_win.geometry(f"+{x}+{y}")
        alert_win.resizable(False, False)
        # 置于顶层
        alert_win.attributes("-topmost", True)
        color = "red"
        if price <= alert_price:
            color = "green"

        ttk.Label(
            alert_win,
            text=f"股票 {name}({code}) 触发预警！",
            font=("Microsoft YaHei", 14, "bold"),
            foreground=color,
        ).pack(pady=(20, 10))

        ttk.Label(
            alert_win,
            text=f"当前价格: ¥{price:.2f}",
            font=("Microsoft YaHei", 12),
        ).pack()

        ttk.Label(
            alert_win,
            text=f"预警价格: ¥{alert_price:.2f}",
            font=("Microsoft YaHei", 12),
        ).pack()

        ttk.Button(alert_win, text="关闭", command=alert_win.destroy).pack(pady=15)

        # 3 秒后自动关闭
        alert_win.after(3000, alert_win.destroy)

    # ---------- 交互事件 ----------
    def _on_double_click(self, event):
        """双击表格行修改预警价格"""
        selected = self.tree.selection()
        if not selected:
            return
        item = selected[0]
        values = self.tree.item(item, "values")
        if not values:
            return
        code = values[0]
        name = values[1]

        # 弹出修改窗口
        edit_win = tk.Toplevel(self.root)
        edit_win.title(f"修改预警价格 - {code}")
        edit_win.geometry("300x120")
        edit_win.resizable(False, False)
         # 获取主窗口的位置和尺寸
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()

        # 获取子窗口的尺寸
        top_w = edit_win.winfo_width()
        top_h = edit_win.winfo_height()

        # 计算子窗口左上角坐标
        x = root_x + (root_w - top_w) // 2
        y = root_y + (root_h - top_h) // 2
        # 设置子窗口位置
        edit_win.geometry(f"+{x}+{y}")

        ttk.Label(edit_win, text=f"股票代码: {name}[{code}]", font=("Microsoft YaHei", 11)).pack(pady=(10, 5))
        ttk.Label(edit_win, text="预警价格(涨>0,跌<0):").pack()
        price_var = tk.StringVar()
        price_entry = ttk.Entry(edit_win, textvariable=price_var, width=15)
        price_entry.pack(pady=5)
        price_entry.focus()

        def save_alert():
            val = price_var.get().strip()
            try:
                new_price = float(val) if val else None
            except ValueError:
                messagebox.showwarning("提示", "价格格式不正确")
                return
            if code in self.config.get("stocks", {}):
                if new_price > 0:
                    self.config["stocks"][code]["alert_price_up"] = new_price
                else:
                    self.config["stocks"][code]["alert_price_down"] = -new_price
            save_config(self.config)
            edit_win.destroy()
            self.refresh_data()
            self.status_var.set(f"已更新 {code} 预警价格")

        ttk.Button(edit_win, text="保存", command=save_alert).pack(pady=0)

    def _on_interval_change(self, event=None):
        """刷新间隔变更事件"""
        try:
            val = int(self.interval_var.get())
            if val < 1:
                val = 1
            elif val > 60:
                val = 60
            self.config["refresh_interval"] = val
            save_config(self.config)
            self.status_var.set(f"刷新间隔已更新为 {val} 秒")
        except ValueError:
            pass

    def _schedule_refresh(self):
        """定时调度数据刷新"""
        interval = self.config.get("refresh_interval", DEFAULT_REFRESH_INTERVAL) * 1000
        self.refresh_data()
        self.root.after(interval, self._schedule_refresh)


# ============================================================
# 主程序入口
# ============================================================
def main():
    root = tk.Tk()
    app = StockMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()