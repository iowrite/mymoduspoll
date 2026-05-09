"""
组导航控件
==========
左侧树形列表，支持多层级结构：配置文件 → 组。
可同时加载多个配置文件，每个文件下的组作为子节点显示。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from modbus_modules.config_parser import AppConfig


class GroupNavigator(ttk.Frame):
    """
    组导航树（多层级）

    用法:
        nav = GroupNavigator(parent)
        nav.on_group_selected = lambda filepath, group_name: ...
        nav.add_config("config.json", config)
        nav.select_first()
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        # item_id -> (filepath, group_name)
        self._item_map: dict[str, tuple[str, str]] = {}
        # filepath -> config
        self._configs: dict[str, AppConfig] = {}
        # 回调参数: (filepath, group_name)
        self.on_group_selected: Optional[Callable[[str, str], None]] = None

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
        # 滚动条（垂直）
        vbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=2)
        self.tree.configure(yscrollcommand=vbar.set)

        # 样式
        style = ttk.Style()
        style.configure("Treeview", rowheight=26, font=("微软雅黑", 9))
        style.configure("GroupNav.Treeview", rowheight=26, font=("微软雅黑", 9))
        self.tree.configure(style="GroupNav.Treeview")

        # 配置文件节点样式
        self.tree.tag_configure("config_root", font=("微软雅黑", 9, "bold"))

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
    def add_config(self, filepath: str, config: AppConfig):
        """添加一个配置文件到导航树"""
        self._configs[filepath] = config

        name = os.path.basename(filepath)
        config_id = f"cfg_{filepath}"
        # 插入配置文件根节点
        if not self.tree.exists(config_id):
            self.tree.insert(
                "", tk.END, iid=config_id, text=f"  📁 {name}", tags=("config_root",)
            )
        self.tree.item(config_id, open=True)

        # 插入该文件下的所有组
        group_count = 0
        for group in config.groups:
            if not group.enabled:
                continue
            icon = "✏" if group.function_code == 0x10 else "📖"
            group_id = f"grp_{filepath}_{group.name}"
            self.tree.insert(
                config_id, tk.END, iid=group_id, text=f"    {icon} {group.name}"
            )
            self._item_map[group_id] = (filepath, group.name)
            group_count += 1

        # 更新计数
        total_groups = len(self._item_map)
        total_configs = len(self._configs)
        self._info_var.set(f"{total_configs} 个配置, {total_groups} 个组")

    def _format_group_label(self, group) -> str:
        icon = "✏" if group.function_code == 0x10 else "📖"
        return f"  {icon} {group.name}"

    # ---------- 选中操作 ----------
    def select_group(self, filepath: str, group_name: str):
        """选中指定配置文件下的指定组"""
        group_id = f"grp_{filepath}_{group_name}"
        if self.tree.exists(group_id):
            self.tree.selection_set(group_id)
            self.tree.see(group_id)
            if self.on_group_selected:
                self.on_group_selected(filepath, group_name)

    def select_first(self):
        """选中第一个组，并触发回调"""
        for item in self.tree.get_children():
            for child in self.tree.get_children(item):
                if child in self._item_map:
                    filepath, group_name = self._item_map[child]
                    self.tree.selection_set(child)
                    self.tree.see(child)
                    if self.on_group_selected:
                        self.on_group_selected(filepath, group_name)
                    return

    def get_selected(self) -> Optional[tuple[str, str]]:
        """获取当前选中的 (filepath, group_name)，如果不是组节点则返回 None"""
        sel = self.tree.selection()
        if not sel:
            return None
        item = sel[0]
        return self._item_map.get(item)

    def get_config(self, filepath: str) -> Optional[AppConfig]:
        """根据文件路径获取配置对象"""
        return self._configs.get(filepath)

    def get_active_config(self) -> Optional[AppConfig]:
        """获取当前选中组所属的配置对象"""
        sel = self.get_selected()
        if sel:
            filepath, _ = sel
            return self._configs.get(filepath)
        return None

    # ---------- 回调 ----------
    def _on_select(self, event):
        """选中变化时通知外部"""
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        # 如果点击的是组节点，触发回调
        if item in self._item_map:
            filepath, group_name = self._item_map[item]
            if self.on_group_selected:
                self.on_group_selected(filepath, group_name)

    def clear(self):
        """清空所有"""
        self.tree.delete(*self.tree.get_children())
        self._item_map.clear()
        self._configs.clear()
        self._info_var.set("未加载配置")

    def clear_config(self, filepath: str):
        """移除指定配置文件及其所有组"""
        config_id = f"cfg_{filepath}"
        if self.tree.exists(config_id):
            # 移除该配置下的所有组映射
            to_remove = [k for k, v in self._item_map.items() if v[0] == filepath]
            for k in to_remove:
                del self._item_map[k]
            self.tree.delete(config_id)
            del self._configs[filepath]
        total_groups = len(self._item_map)
        total_configs = len(self._configs)
        if total_configs == 0:
            self._info_var.set("未加载配置")
        else:
            self._info_var.set(f"{total_configs} 个配置, {total_groups} 个组")
