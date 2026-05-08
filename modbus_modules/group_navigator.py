"""
组导航控件
==========
左侧树形列表，显示配置文件中的所有组。
点击选中一个组，右侧数据表切换显示该组的点位。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from modbus_modules.config_parser import AppConfig


class GroupNavigator(ttk.Frame):
    """
    组导航树

    用法:
        nav = GroupNavigator(parent)
        nav.on_group_selected = lambda name: ...
        nav.load_groups(config)
        nav.select_first()
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._config: Optional[AppConfig] = None
        self._item_map: dict[str, str] = {}  # group_name -> item_id
        self.on_group_selected: Optional[Callable[[str], None]] = None

        self._build_ui()

    def _build_ui(self):
        # 标题
        lbl = ttk.Label(self, text="📋 组列表", font=("微软雅黑", 9, "bold"))
        lbl.pack(fill=tk.X, padx=4, pady=(0, 2))

        # 树
        self.tree = ttk.Treeview(
            self,
            show="tree",
            selectmode="browse",
            height=12,
        )
        self.tree.pack(fill=tk.BOTH, expand=True, padx=2)

        # 滚动条
        vbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=vbar.set)

        # 样式
        style = ttk.Style()
        style.configure("Treeview", rowheight=28, font=("微软雅黑", 9))
        style.configure("GroupNav.Treeview", rowheight=28, font=("微软雅黑", 9))
        self.tree.configure(style="GroupNav.Treeview")

        # 点击选中事件
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # 提示文字
        self._info_var = tk.StringVar(value="未加载配置")
        info = ttk.Label(
            self,
            textvariable=self._info_var,
            foreground="gray",
            font=("微软雅黑", 8),
            anchor=tk.CENTER,
        )
        info.pack(fill=tk.X, padx=4, pady=(2, 0))

    # ---------- 配置加载 ----------
    def load_groups(self, config: AppConfig):
        """加载组列表"""
        self._config = config
        self._item_map.clear()
        self.tree.delete(*self.tree.get_children())

        for group in config.groups:
            if not group.enabled:
                continue
            label = self._format_group_label(group)
            item_id = f"nav_{group.name}"
            self.tree.insert("", tk.END, iid=item_id, text=label)
            self._item_map[group.name] = item_id

        self._info_var.set(f"{len(self._item_map)} 个组")

    def _format_group_label(self, group) -> str:
        """格式化组显示文字"""
        if group.function_code == 0x10:
            return f"  ✏ {group.name}  (写寄存器)"
        func_name = {3: "读寄存器", 4: "读输入寄存器"}.get(
            group.function_code, f"FC{group.function_code}"
        )
        return f"  📖 {group.name}  ({func_name})"

    # ---------- 选中操作 ----------
    def select_group(self, group_name: str):
        """选中指定组"""
        item_id = self._item_map.get(group_name)
        if item_id and self.tree.exists(item_id):
            self.tree.selection_set(item_id)
            self.tree.see(item_id)
            # 程序设置选中不会触发 <<TreeviewSelect>>，手动回调
            if self.on_group_selected:
                self.on_group_selected(group_name)

    def select_first(self):
        """选中第一个组，并触发回调"""
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.see(children[0])
            # 找到对应的组名并回调
            for name, item_id in self._item_map.items():
                if item_id == children[0]:
                    if self.on_group_selected:
                        self.on_group_selected(name)
                    break

    def get_selected(self) -> Optional[str]:
        """获取当前选中的组名"""
        sel = self.tree.selection()
        if not sel:
            return None
        # 反向查找
        for name, item_id in self._item_map.items():
            if item_id == sel[0]:
                return name
        return None

    # ---------- 回调 ----------
    def _on_select(self, event):
        """选中变化时通知外部"""
        name = self.get_selected()
        if name and self.on_group_selected:
            self.on_group_selected(name)

    def clear(self):
        """清空"""
        self.tree.delete(*self.tree.get_children())
        self._item_map.clear()
        self._info_var.set("未加载配置")
