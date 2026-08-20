import tkinter as tk
from tkinter import simpledialog, ttk, messagebox, CENTER
import threading
import time
import json
import os
import sys
from datetime import datetime
import tushare as ts


def restart_program():
    """重启当前程序"""
    print("程序正在重启...")
    python = sys.executable
    os.execv(python, [python] + sys.argv)


def fixed_map(style, option):
    """过滤掉干扰颜色设置的样式规则"""
    return [elm for elm in style.map('Treeview', query_opt=option)
            if elm[:2] != ('!disabled', '!selected')]


class StockWatchApp:
    """
    股票盯盘助手主类
    """

    CONFIG_FILE = "stock_watch_config.json"

    def __init__(self, root):
        """初始化应用程序"""
        self.root = root
        self.root.title("stock-radar")
        self.root.geometry("1150x700")

        style = ttk.Style()
        style.configure("Treeview", rowheight=52, font=('微软雅黑', 11))
        style.theme_use('vista')
        style.map('Treeview',
                  foreground=fixed_map(style, 'foreground'),
                  background=fixed_map(style, 'background'))

        # 存储所有页签的数据
        self.tabs = {}

        # 更新线程控制标志
        self.running = True

        # 上次预警记录，避免重复弹窗
        self.last_alert_dict = {}

        # 创建顶部操作区域
        self.create_top_frame()

        # 创建Notebook（页签容器）
        self.create_sort_frame()

        # 创建Notebook（页签容器）
        self.create_notebook()

        # 加载配置文件，若不存在则创建默认页签
        self.load_config_or_default()

        # 启动数据获取线程
        self.start_update_thread()

        # 启动时钟更新
        self.update_clock()

        # 绑定窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_top_frame(self):
        """创建顶部操作区域"""
        self.top_frame = ttk.Frame(self.root)
        self.top_frame.pack(fill=tk.X, padx=10, pady=10)

        # 左侧控件区域
        left_frame = ttk.Frame(self.top_frame)
        left_frame.pack(side=tk.LEFT)

        ttk.Label(left_frame, text="股票代码:").pack(side=tk.LEFT, padx=(0, 5))
        self.code_entry = ttk.Entry(left_frame, width=12)
        self.code_entry.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(left_frame, text="预警涨价:").pack(side=tk.LEFT, padx=(0, 5))
        self.up_warning_entry = ttk.Entry(left_frame, width=8)
        self.up_warning_entry.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(left_frame, text="元").pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(left_frame, text="预警跌价:").pack(side=tk.LEFT, padx=(0, 5))
        self.down_warning_entry = ttk.Entry(left_frame, width=8)
        self.down_warning_entry.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(left_frame, text="元").pack(side=tk.LEFT, padx=(0, 15))

        # ---- 新增成本价输入框 ----
        ttk.Label(left_frame, text="成本价:").pack(side=tk.LEFT, padx=(0, 5))
        self.buy_price_entry = ttk.Entry(left_frame, width=8)
        self.buy_price_entry.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(left_frame, text="元").pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(left_frame, text="添加到页签:").pack(side=tk.LEFT, padx=(0, 5))
        self.target_tab_var = tk.StringVar()
        self.target_tab_combo = ttk.Combobox(left_frame, textvariable=self.target_tab_var, width=10)
        self.target_tab_combo.pack(side=tk.LEFT, padx=(0, 10))

        add_btn = tk.Button(left_frame, text="添加股票", bg="lightblue", command=self.add_stock)
        add_btn.pack(side=tk.LEFT, padx=(0, 10))

        del_btn = tk.Button(left_frame, text="删除股票", bg="lightcoral", command=self.delete_selected_stock)
        del_btn.pack(side=tk.LEFT, padx=(0, 10))

        add_tab_btn = tk.Button(left_frame, text="新增页签", bg="lightblue", command=self.add_new_tab)
        add_tab_btn.pack(side=tk.LEFT, padx=(20, 0))

        del_tab_btn = tk.Button(left_frame, text="删除页签", bg="lightcoral", command=self.delete_current_tab)
        del_tab_btn.pack(side=tk.LEFT, padx=(5, 0))

        # 排序方式区域（第二行）
        self.clock_label = ttk.Label(self.top_frame, font=('Arial', 12, 'bold'), foreground='blue')
        self.clock_label.pack(side=tk.RIGHT, padx=10)

    def create_sort_frame(self):
        """创建排序方式区域（位于操作栏和表格之间）"""
        sort_frame = ttk.Frame(self.root)
        sort_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        ttk.Label(sort_frame, text="排序方式:").pack(side=tk.LEFT, padx=(0, 10))

        self.sort_var = tk.StringVar(value="none")

        sort_options = [
            ("默认", "none"),
            ("价格↓", "price_desc"),
            ("价格↑", "price_asc"),
            ("涨幅↓", "change_desc"),
            ("涨幅↑", "change_asc"),
        ]
        for text, value in sort_options:
            rb = ttk.Radiobutton(sort_frame, text=text, variable=self.sort_var, value=value,
                                 command=self.on_sort_changed)
            rb.pack(side=tk.LEFT, padx=(0, 15))

    def create_notebook(self):
        """创建页签容器"""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def on_tab_changed(self, event):
        """页签切换时更新下拉框选项"""
        self.update_tab_combo()

    def update_tab_combo(self):
        """更新目标页签下拉框的选项"""
        tab_names = list(self.tabs.keys())
        self.target_tab_combo['values'] = tab_names
        if tab_names:
            current_idx = self.notebook.index(self.notebook.select())
            if current_idx < len(tab_names):
                self.target_tab_var.set(tab_names[current_idx])

    def add_tab_page(self, tab_name):
        """添加一个新的页签页面"""
        tab_frame = ttk.Frame(self.notebook)

        columns = ('序号', '名称', '涨跌', '现价', '成本价', '低价', '高价', '昨价', '预警涨价', '预警跌价')
        tree = ttk.Treeview(tab_frame, columns=columns, show='headings')

        # 各列宽度
        col_widths = {
            '序号': 50, '名称': 100, '涨跌': 100, '现价': 90,
            '成本价': 80,               # 新增列宽
            '低价': 70, '高价': 70, '昨价': 70, '预警涨价': 80,
            '预警跌价': 80
        }

        for col in columns:
            tree.heading(col, text=col, command=lambda c=col: self.sort_by_column(tree, c))
            tree.column(col, width=col_widths.get(col, 60), anchor=CENTER)

        v_scroll = ttk.Scrollbar(tab_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=v_scroll.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tree.bind("<Double-1>", lambda event, t=tree, tn=tab_name: self.on_tree_double_click(event, t, tn))

        # Canvas覆盖层
        change_canvas = tk.Canvas(tab_frame, highlightthickness=0, bd=0)
        change_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.tabs[tab_name] = {
            'name': tab_name,
            'data': [],
            'tree': tree,
            'frame': tab_frame,
            'change_canvas': change_canvas
        }
        self.notebook.add(tab_frame, text=tab_name)

        change_canvas.tk.call('raise', change_canvas._w)

        def _on_canvas_double_click(event, t=tree, tn=tab_name, cv=change_canvas):
            oy = t.winfo_rooty() - cv.winfo_rooty()
            event_y = event.y + oy
            item_id = t.identify_row(event_y)
            if not item_id:
                return
            values = t.item(item_id, 'values')
            if not values:
                return
            idx = int(values[0]) - 1
            data = self.tabs[tn]['data']
            if 0 <= idx < len(data):
                self.edit_stock_warning(tn, idx, data[idx])
        change_canvas.bind("<Double-1>", _on_canvas_double_click)

        def _on_canvas_click(event, t=tree, tn=tab_name, cv=change_canvas):
            oy = t.winfo_rooty() - cv.winfo_rooty()
            item_id = t.identify_row(event.y + oy)
            if item_id:
                cur_sel = set(t.selection())
                if event.state & 0x4:  # Ctrl
                    if item_id in cur_sel:
                        t.selection_remove(item_id)
                    else:
                        t.selection_add(item_id)
                elif item_id in cur_sel and len(cur_sel) == 1:
                    t.selection_remove(item_id)
                else:
                    t.selection_set(item_id)
                t.focus(item_id)
                self.render_change_column_colors(tn)
        change_canvas.bind("<ButtonPress-1>", _on_canvas_click)

        def _on_mousewheel(event):
            tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
            self.render_change_column_colors(tab_name)
        change_canvas.bind("<MouseWheel>", _on_mousewheel)

        _orig_yscroll = v_scroll.cget('command')
        def _on_scroll(*args):
            _orig_yscroll(*args)
            self.render_change_column_colors(tab_name)
        v_scroll.config(command=_on_scroll)

        tab_frame.bind('<Configure>', lambda e, tn=tab_name: self._schedule_render(tn), add='+')

    def _schedule_render(self, tab_name):
        tab = self.tabs.get(tab_name)
        if tab:
            tab['_render_pending'] = True
            self.root.after_idle(lambda: self._do_scheduled_render(tab_name))

    def _do_scheduled_render(self, tab_name):
        tab = self.tabs.get(tab_name)
        if tab and tab.get('_render_pending'):
            tab['_render_pending'] = False
            self.render_change_column_colors(tab_name)

    def on_tree_double_click(self, event, tree, tab_name):
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        values = tree.item(item_id, 'values')
        if not values:
            return
        idx = int(values[0]) - 1
        data = self.tabs[tab_name]['data']
        if idx < 0 or idx >= len(data):
            return
        stock = data[idx]
        self.edit_stock_warning(tab_name, idx, stock)

    def edit_stock_warning(self, tab_name, idx, stock):
        """弹出对话框修改预警价格及成本价"""
        code = stock['code']
        name = stock.get('name', code)

        dialog = tk.Toplevel(self.root)
        dialog.title(f"修改股票信息 - {name}({code})")
        dialog.geometry("350x280")
        dialog.resizable(False, False)
        dialog.attributes('-topmost', True)

        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        width, height = 350, 280
        x = parent_x + (parent_w - width) // 2
        y = parent_y + (parent_h - height) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        cur_up = stock.get('up_warning')
        cur_down = stock.get('down_warning')
        cur_buy = stock.get('buy_price')

        # 成本价输入框（新增）
        tk.Label(dialog, text="成本价（元）:").pack(pady=(20, 5))
        buy_entry = tk.Entry(dialog, width=15)
        buy_entry.insert(0, str(cur_buy) if cur_buy is not None else "")
        buy_entry.pack()

        tk.Label(dialog, text="预警涨价（元）:").pack(pady=(10, 5))
        up_entry = tk.Entry(dialog, width=15)
        up_entry.insert(0, str(cur_up) if cur_up is not None else "")
        up_entry.pack()

        tk.Label(dialog, text="预警跌价（元）:").pack(pady=(10, 5))
        down_entry = tk.Entry(dialog, width=15)
        down_entry.insert(0, str(cur_down) if cur_down is not None else "")
        down_entry.pack()

        def save():
            # 处理成本价
            buy_text = buy_entry.get().strip()
            buy_price_val = None
            if buy_text:
                try:
                    buy_price_val = float(buy_text)
                    if buy_price_val <= 0:
                        messagebox.showwarning("警告", "成本价必须大于0！")
                        return
                except ValueError:
                    messagebox.showwarning("警告", "成本价请输入有效数字！")
                    return

            # 处理预警涨价
            up_text = up_entry.get().strip()
            up_warning = None
            if up_text:
                try:
                    up_warning = float(up_text)
                    if up_warning <= 0:
                        messagebox.showwarning("警告", "预警涨价必须大于0！")
                        return
                except ValueError:
                    messagebox.showwarning("警告", "预警涨价请输入有效数字！")
                    return

            # 处理预警跌价
            down_text = down_entry.get().strip()
            down_warning = None
            if down_text:
                try:
                    down_warning = float(down_text)
                    if down_warning <= 0:
                        messagebox.showwarning("警告", "预警跌价必须大于0！")
                        return
                except ValueError:
                    messagebox.showwarning("警告", "预警跌价请输入有效数字！")
                    return

            # 更新股票数据
            stock['buy_price'] = buy_price_val
            stock['up_warning'] = up_warning
            stock['down_warning'] = down_warning

            self.update_tab_display(tab_name)
            self.save_config()
            dialog.destroy()
            messagebox.showinfo("提示", f"股票 {name}({code}) 信息已更新")

        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=20)
        tk.Button(btn_frame, text="保存", command=save, width=8).pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="取消", command=dialog.destroy, width=8).pack(side=tk.LEFT)

    def add_new_tab(self):
        default_name = f"页签{len(self.tabs) + 1}"
        name = simpledialog.askstring("新增页签", "请输入页签名称:", initialvalue=default_name)
        if name and name.strip():
            name = name.strip()
            if name in self.tabs:
                messagebox.showwarning("提示", f"页签 [{name}] 已存在！")
                return
            self.add_tab_page(name)
            self.update_tab_combo()
            self.save_config()
            messagebox.showinfo("提示", f"页签 [{name}] 创建成功！")

    def delete_current_tab(self):
        current_idx = self.notebook.index(self.notebook.select())
        tab_names = list(self.tabs.keys())

        if current_idx < len(tab_names):
            tab_name = tab_names[current_idx]
            if tab_name in ["自选", "持仓"]:
                messagebox.showwarning("提示", "默认页签 [自选] 和 [持仓] 不可删除！")
                return

            result = messagebox.askyesno("确认删除", f"确定要删除页签 [{tab_name}] 及其所有股票数据吗？")
            if result:
                self.notebook.forget(current_idx)
                del self.tabs[tab_name]
                self.update_tab_combo()
                self.save_config()
                messagebox.showinfo("提示", f"页签 [{tab_name}] 已删除！")

    def add_stock(self):
        """添加股票（含成本价）"""
        addCode = self.code_entry.get().strip()
        if not addCode:
            messagebox.showinfo("提示", "请输入股票代码！")
            return

        up_price = self.up_warning_entry.get().strip()
        down_price = self.down_warning_entry.get().strip()
        buy_price = self.buy_price_entry.get().strip()

        up_warning = None
        down_warning = None
        buy_price_val = None

        if up_price:
            try:
                up_warning = float(up_price)
                if up_warning <= 0:
                    messagebox.showinfo("提示", "预警涨价必须为大于0的数字！")
                    return
            except ValueError:
                messagebox.showinfo("提示", "预警涨价请输入有效的数字！")
                return

        if down_price:
            try:
                down_warning = float(down_price)
                if down_warning <= 0:
                    messagebox.showinfo("提示", "预警跌价必须为大于0的数字！")
                    return
            except ValueError:
                messagebox.showinfo("提示", "预警跌价请输入有效的数字！")
                return

        if buy_price:
            try:
                buy_price_val = float(buy_price)
                if buy_price_val <= 0:
                    messagebox.showinfo("提示", "成本价必须为大于0的数字！")
                    return
            except ValueError:
                messagebox.showinfo("提示", "成本价请输入有效的数字！")
                return

        target_tab = self.target_tab_var.get()
        if not target_tab or target_tab not in self.tabs:
            messagebox.showinfo("提示", "请选择有效的目标页签！")
            return

        try:
            stock = ts.get_realtime_quotes(addCode)
        except Exception as e:
            messagebox.showinfo("提示", f"接口调用失败: {e}")
            return

        if stock is None or stock.empty:
            messagebox.showinfo("提示", "代码{0}错误".format(addCode))
            return

        current_data = self.tabs[target_tab]['data']
        for s in current_data:
            if s.get('code') == addCode:
                messagebox.showinfo("提示", f"股票 [{addCode}] 已在当前页签中！")
                return

        try:
            name = stock['name'].iloc[0]
        except:
            name = addCode

        new_stock = {
            'code': addCode,
            'name': name,
            'up_warning': up_warning,
            'down_warning': down_warning,
            'buy_price': buy_price_val
        }
        current_data.append(new_stock)

        self.update_tab_display(target_tab)
        self.save_config()

        self.code_entry.delete(0, tk.END)
        self.up_warning_entry.delete(0, tk.END)
        self.down_warning_entry.delete(0, tk.END)
        self.buy_price_entry.delete(0, tk.END)

        messagebox.showinfo("提示", f"股票 [{name}({addCode})] 已添加到页签 [{target_tab}]！")

    def delete_selected_stock(self):
        current_idx = self.notebook.index(self.notebook.select())
        tab_names = list(self.tabs.keys())
        if current_idx >= len(tab_names):
            return

        current_tab = tab_names[current_idx]
        tree = self.tabs[current_tab]['tree']

        selected = tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选中要删除的股票！")
            return

        selected_items = list(selected)
        data = self.tabs[current_tab]['data']
        indices_to_remove = []
        for item in selected_items:
            values = tree.item(item, 'values')
            if values:
                idx_value = values[0]
                for i, stock in enumerate(data):
                    if i + 1 == int(idx_value):
                        indices_to_remove.append(i)
                        break

        for idx in sorted(indices_to_remove, reverse=True):
            del data[idx]

        self.update_tab_display(current_tab)
        self.save_config()

    def _extract_sort_value(self, col, value):
        """从多行显示文本中提取排序用的实际值（支持成本价列）"""
        if not value or value == '--':
            return '' if col not in ['涨跌'] else 0

        if col == '名称':
            return value.split('\n')[0]
        elif col == '涨跌':
            first_line = value.split('\n')[0]
            try:
                return float(first_line.replace('%', ''))
            except:
                return first_line
        elif col == '成本价':
            # 取第一行（价格）转浮点数
            first_line = value.split('\n')[0]
            try:
                return float(first_line)
            except:
                return 0
        else:
            try:
                return float(value)
            except:
                return value

    def sort_by_column(self, tree, col):
        data_list = []
        for child in tree.get_children(''):
            v = tree.set(child, col)
            key = self._extract_sort_value(col, v)
            data_list.append((key, child))
        data_list.sort(key=lambda x: x[0])
        for idx, (_, child) in enumerate(data_list):
            tree.move(child, '', idx)
        tree.heading(col, command=lambda: self.reverse_sort(tree, col))
        self._rerender_after_sort(tree)

    def reverse_sort(self, tree, col):
        data_list = []
        for child in tree.get_children(''):
            v = tree.set(child, col)
            key = self._extract_sort_value(col, v)
            data_list.append((key, child))
        data_list.sort(key=lambda x: x[0], reverse=True)
        for idx, (_, child) in enumerate(data_list):
            tree.move(child, '', idx)
        tree.heading(col, command=lambda: self.sort_by_column(tree, col))
        self._rerender_after_sort(tree)

    def _rerender_after_sort(self, tree):
        for tab_name, tab in self.tabs.items():
            if tab['tree'] is tree:
                self.root.after_idle(lambda tn=tab_name: self.render_change_column_colors(tn))
                break

    def update_tab_display(self, tab_name):
        """更新指定页签的表格显示（成本价下显示盈亏比例）"""
        tab = self.tabs.get(tab_name)
        if not tab:
            return

        tree = tab['tree']
        data = tab['data']

        selected_codes = set()
        for item_id in tree.selection():
            vals = tree.item(item_id, 'values')
            if vals:
                idx_in_data = int(vals[0]) - 1
                if 0 <= idx_in_data < len(data):
                    selected_codes.add(data[idx_in_data]['code'])

        self._apply_sort_to_data(data)

        for item in tree.get_children():
            tree.delete(item)

        for idx, stock in enumerate(data, 1):
            real_time = stock.get('real_time', {})
            code = stock['code']
            name = stock.get('name', '--')
            name_display = f"{name}\n[{code}]"

            change_pct = real_time.get('涨跌', '--')
            change_amount = real_time.get('涨跌额', '--')
            change_display = f"{change_pct}\n[{change_amount}]"

            price = real_time.get('现价', '--')
            pre_close = real_time.get('昨价', '--')

            # ---- 构造成本价显示，包含盈亏比例 ----
            buy_price = stock.get('buy_price')
            if buy_price is not None:
                price_str = real_time.get('现价', '--')
                if price_str != '--':
                    try:
                        current_price = float(price_str)
                        profit_pct = (current_price - buy_price) / buy_price * 100
                        buy_display = f"{buy_price:.3f}\n盈亏: {profit_pct:+.2f}%"
                    except (ValueError, ZeroDivisionError):
                        buy_display = f"{buy_price:.3f}\n盈亏: --"
                else:
                    buy_display = f"{buy_price:.3f}\n盈亏: --"
            else:
                buy_display = '-'

            values = (
                idx,
                name_display,
                change_display,
                price,
                buy_display,
                real_time.get('低价', '--'),
                real_time.get('高价', '--'),
                real_time.get('昨价', '--'),
                stock.get('up_warning', '--') if stock.get('up_warning') is not None else '未设',
                stock.get('down_warning', '--') if stock.get('down_warning') is not None else '未设'
            )

            item_id = tree.insert('', 'end', values=values)
            if code in selected_codes:
                tree.selection_add(item_id)

        if 'change_canvas' in tab:
            self.root.after_idle(lambda tn=tab_name: self.render_change_column_colors(tn))

    def _get_stock_sort_value(self, stock, sort_mode):
        if sort_mode in ('price_desc', 'price_asc'):
            return stock.get('price', 0) or 0
        elif sort_mode in ('change_desc', 'change_asc'):
            real_time = stock.get('real_time', {})
            change_pct_str = real_time.get('涨跌', '--')
            if change_pct_str == '--':
                return 0
            try:
                return float(change_pct_str.replace('%', ''))
            except (ValueError, AttributeError):
                return 0
        return 0

    def _apply_sort_to_data(self, data):
        if not hasattr(self, 'sort_var'):
            return
        sort_mode = self.sort_var.get()
        if sort_mode == 'none':
            return
        reverse = sort_mode in ('price_desc', 'change_desc')
        data.sort(key=lambda s: self._get_stock_sort_value(s, sort_mode), reverse=reverse)

    def on_sort_changed(self):
        current_idx = self.notebook.index(self.notebook.select())
        tab_names = list(self.tabs.keys())
        if current_idx < len(tab_names):
            tab_name = tab_names[current_idx]
            self.update_tab_display(tab_name)

    def render_change_column_colors(self, tab_name):
        """使用Canvas覆盖层重绘所有单元格，支持多行文本显示"""
        tab = self.tabs.get(tab_name)
        if not tab:
            return

        tree = tab['tree']
        canvas = tab.get('change_canvas')

        if not canvas or not tree.winfo_exists():
            return

        canvas.delete("cell")

        items = tree.get_children()
        if not items:
            return

        tree_x = tree.winfo_rootx()
        tree_y = tree.winfo_rooty()
        canvas_x = canvas.winfo_rootx()
        canvas_y = canvas.winfo_rooty()
        off_x = tree_x - canvas_x
        off_y = tree_y - canvas_y

        bg_normal = '#F0F0F0'
        bg_selected = '#0078D7'
        text_normal = '#0000FF'
        text_selected = '#FFFFFF'
        font_family = ('微软雅黑', 11)
        columns = ('#1', '#2', '#3', '#4', '#5', '#6', '#7', '#8', '#9', '#10')
        change_col = '#3'
        selected_items = set(tree.selection())

        first_item = items[0]
        for col_id in columns:
            cell_bbox = tree.bbox(first_item, col_id)
            if not cell_bbox:
                continue
            cx, cy, cw, ch = cell_bbox

            header_h = cy
            if header_h > 0:
                canvas.create_rectangle(
                    cx + off_x, off_y, cx + off_x + cw, off_y + header_h,
                    fill='#F0F0F0', outline='#E0E0E0', tags="cell"
                )
                heading_text = tree.heading(col_id, 'text')
                canvas.create_text(
                    cx + off_x + cw // 2, off_y + header_h // 2,
                    text=str(heading_text),
                    fill='#000000',
                    font=('微软雅黑', 11, 'bold'),
                    anchor=CENTER, tags="cell"
                )

        for item_id in items:
            values = tree.item(item_id, 'values')
            if not values:
                continue

            is_selected = item_id in selected_items
            bg = bg_selected if is_selected else bg_normal
            default_text_color = text_selected if is_selected else text_normal

            for ci, col_id in enumerate(columns):
                bbox = tree.bbox(item_id, col_id)
                if not bbox:
                    continue
                x, y, w, h = bbox

                canvas.create_rectangle(
                    x + off_x, y + off_y, x + off_x + w, y + off_y + h,
                    fill=bg, outline='#E0E0E0', tags="cell"
                )

                text = str(values[ci])
                text_color = default_text_color
                if col_id == change_col:
                    first_line = text.split('\n')[0]
                    if first_line and first_line != '--':
                        try:
                            change_val = float(first_line.replace('%', ''))
                            if change_val > 0:
                                text_color = 'red' if not is_selected else '#FF6666'
                            elif change_val < 0:
                                text_color = 'green' if not is_selected else '#66FF66'
                        except (ValueError, AttributeError):
                            pass

                # ---- 支持多行文本绘制 ----
                if '\n' in text:
                    lines = text.split('\n')
                    num_lines = len(lines)
                    line_height = h / num_lines
                    start_y = y + off_y
                    for i, line in enumerate(lines):
                        canvas.create_text(
                            x + off_x + w // 2, start_y + (i + 0.5) * line_height,
                            text=line.strip(),
                            fill=text_color,
                            font=font_family,
                            anchor=CENTER, tags="cell"
                        )
                else:
                    canvas.create_text(
                        x + off_x + w // 2, y + off_y + h // 2,
                        text=text,
                        fill=text_color,
                        font=font_family,
                        anchor=CENTER, tags="cell"
                    )

    def start_update_thread(self):
        self.update_thread = threading.Thread(target=self.update_data_loop, daemon=True)
        self.update_thread.start()

    def update_data_loop(self):
        while self.running:
            self.fetch_all_data()
            time.sleep(1)

    def fetch_all_data(self):
        tab_names = list(self.tabs.keys())
        for tab_name in tab_names:
            try:
                tab = self.tabs[tab_name]
                data = tab['data']
                if not data:
                    continue

                codes = [stock['code'] for stock in data]
                try:
                    quotes = ts.get_realtime_quotes(codes)
                except Exception as e:
                    print(f"获取数据失败: {e}")
                    continue

                if quotes is None or quotes.empty:
                    continue

                for idx, stock in enumerate(data):
                    code = stock['code']
                    quote_row = quotes[quotes['code'] == code]
                    if quote_row.empty:
                        continue

                    row = quote_row.iloc[0]

                    try:
                        price = float(row['price']) if row['price'] else 0
                        pre_close = float(row['pre_close']) if row['pre_close'] else 0
                        low = float(row['low']) if row['low'] else 0
                        high = float(row['high']) if row['high'] else 0
                        change_pct = (price - pre_close) / pre_close * 100 if pre_close != 0 else 0
                        change_amount = price - pre_close
                        real_time = {
                            '现价': f"{price:.3f}" if price != 0 else '--',
                            '涨跌': f"{change_pct:+.2f}%" if pre_close != 0 else '--',
                            '涨跌额': f"{change_amount:+.3f}" if pre_close != 0 else '--',
                            '低价': f"{low:.2f}" if low != 0 else '--',
                            '高价': f"{high:.2f}" if high != 0 else '--',
                            '昨价': f"{pre_close:.2f}" if pre_close != 0 else '--'
                        }
                        stock['real_time'] = real_time
                        stock['name'] = row['name'] if row['name'] else stock['name']
                        stock['price'] = price
                        stock['pre_close'] = pre_close
                        self.check_price_warning(tab_name, stock, price, stock.get('up_warning'), stock.get('down_warning'))
                    except (ValueError, TypeError) as e:
                        print(f"解析数据错误: {code}, {e}")
                        continue
                self.root.after(0, lambda tn=tab_name: self.update_tab_display(tn))
            except Exception as e:
                print(f"处理页签 {tab_name} 数据时出错: {e}")

    def check_price_warning(self, tab_name, stock, current_price, up_warning_price, down_warning_price):
        code = stock['code']
        name = stock.get('name', code)

        alert_key = f"{tab_name}_{code}"

        if up_warning_price is not None and current_price is not None:
            if current_price >= up_warning_price:
                last_state = self.last_alert_dict.get(alert_key, {}).get('up', False)
                if not last_state:
                    self.root.after(0, lambda: self.show_alert_window(
                        f"⚠️ 股票预警提示 ⚠️",
                        f"{name}({code})\n当前价格: {current_price:.2f}元\n预警涨价: {up_warning_price:.2f}元",
                        color='red'
                    ))
                    if alert_key not in self.last_alert_dict:
                        self.last_alert_dict[alert_key] = {}
                    self.last_alert_dict[alert_key]['up'] = True
            else:
                if alert_key in self.last_alert_dict:
                    self.last_alert_dict[alert_key]['up'] = False

        if down_warning_price is not None and current_price is not None:
            if current_price <= down_warning_price:
                last_state = self.last_alert_dict.get(alert_key, {}).get('down', False)
                if not last_state:
                    self.root.after(0, lambda: self.show_alert_window(
                        f"⚠️ 股票预警提示 ⚠️",
                        f"{name}({code})\n当前价格: {current_price:.2f}元\n预警跌价: {down_warning_price:.2f}元",
                        color='green'
                    ))
                    if alert_key not in self.last_alert_dict:
                        self.last_alert_dict[alert_key] = {}
                    self.last_alert_dict[alert_key]['down'] = True
            else:
                if alert_key in self.last_alert_dict:
                    self.last_alert_dict[alert_key]['down'] = False

    def show_alert_window(self, title, message, color='red'):
        alert_window = tk.Toplevel(self.root)
        alert_window.title(title)
        alert_window.geometry("350x150")
        alert_window.resizable(False, False)
        alert_window.attributes('-topmost', True)

        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        width, height = 350, 150
        x = parent_x + (parent_w - width) // 2
        y = parent_y + (parent_h - height) // 2
        alert_window.geometry(f"{width}x{height}+{x}+{y}")

        frame = tk.Frame(alert_window, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        label = tk.Label(frame, text=message, font=('Arial', 16), wraplength=300, justify='center')
        label.pack(pady=20)

        if color == 'red':
            label.config(fg='red')
        elif color == 'green':
            label.config(fg='green')

        alert_window.after(3000, alert_window.destroy)

    def update_clock(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.clock_label.config(text=now)
        self.root.after(1000, self.update_clock)

    def save_config(self):
        """保存配置时包含成本价"""
        config_data = {}
        for tab_name, tab_info in self.tabs.items():
            stocks = []
            for stock in tab_info['data']:
                stock_config = {
                    'code': stock['code'],
                    'name': stock.get('name', stock['code']),
                    'up_warning': stock.get('up_warning'),
                    'down_warning': stock.get('down_warning'),
                    'buy_price': stock.get('buy_price')
                }
                stocks.append(stock_config)
            config_data[tab_name] = stocks

        try:
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存配置文件失败: {e}")

    def load_config_or_default(self):
        """加载配置时恢复成本价"""
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                for tab_name, stocks in config_data.items():
                    self.add_tab_page(tab_name)
                    for stock_cfg in stocks:
                        new_stock = {
                            'code': stock_cfg['code'],
                            'name': stock_cfg.get('name', stock_cfg['code']),
                            'up_warning': stock_cfg.get('up_warning'),
                            'down_warning': stock_cfg.get('down_warning'),
                            'buy_price': stock_cfg.get('buy_price')
                        }
                        self.tabs[tab_name]['data'].append(new_stock)
                    self.update_tab_display(tab_name)
                self.update_tab_combo()
                return
            except Exception as e:
                print(f"加载配置文件失败: {e}，将使用默认页签")

        self.add_tab_page("自选")
        self.add_tab_page("持仓")
        self.update_tab_combo()

    def on_closing(self):
        self.running = False
        self.save_config()
        if hasattr(self, 'update_thread'):
            self.update_thread.join(timeout=3)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = StockWatchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()