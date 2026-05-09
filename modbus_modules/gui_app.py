"""
主 GUI 应用模块
===============
简洁、单页面的 Modbus 监控上位机界面。
"""

from __future__ import annotations

import datetime
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from modbus_modules.config_parser import (
    AppConfig,
    load_config,
    validate_config,
)
from modbus_modules.data_monitor import DataMonitor
from modbus_modules.data_table import DataTable
from modbus_modules.group_navigator import GroupNavigator
from modbus_modules.modbus_core import (
    add_crc,
    build_modbus_frame,
    bytes_to_hex_str,
    convert_register_value,
    parse_modbus_response,
)
from modbus_modules.polling_engine import GroupPollResult, PollingEngine
from modbus_modules.serial_manager import SerialManager


class ModbusMasterApp:
    """Modbus 主机上位机主应用"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Modbus 上位机 v2.0")
        self.root.geometry("1300x780")
        self.root.minsize(1000, 620)

        # 核心组件
        self.serial_mgr = SerialManager()
        self.polling_engine = PollingEngine()
        self.app_config: Optional[AppConfig] = None
        self.app_configs: dict[str, AppConfig] = {}
        self.config_filepath: str = ""

        # 数据监控窗口
        self.data_monitor = DataMonitor(self.root, self.serial_mgr)

        # 回调绑定
        self.serial_mgr.on_received = self._on_serial_received
        self.serial_mgr.on_error = self._on_serial_error
        self.serial_mgr.on_status_change = self._on_serial_status_change
        self.polling_engine.on_poll_result = self._on_poll_result
        self.polling_engine.on_status_change = self._on_polling_status_change
        self.polling_engine.send_request = self._send_poll_request

        # 同步锁
        self._sync_response: Optional[bytes] = None
        self._sync_event = threading.Event()
        self._sync_lock = threading.Lock()

        # 写组状态
        self._current_write_group: Optional[str] = None

        self._build_ui()
        self.refresh_ports()
        self._update_ui_state()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        """构建整体界面（单页）"""
        self._build_menu()

        main = ttk.Frame(self.root, padding=5)
        main.pack(fill=tk.BOTH, expand=True)

        # 第1行：串口
        self._build_serial_row(main)
        # 第2行：配置 + 轮循
        self._build_config_row(main)
        # 主体：左侧组导航 + 右侧数据表
        content = ttk.Frame(main)
        content.pack(fill=tk.BOTH, expand=True, pady=(3, 3))

        # 左侧组导航
        nav_frame = ttk.Frame(content, width=240)
        nav_frame.pack(side=tk.LEFT, fill=tk.Y)
        nav_frame.pack_propagate(False)
        self.group_nav = GroupNavigator(nav_frame)
        self.group_nav.pack(fill=tk.BOTH, expand=True)
        self.group_nav.on_group_selected = self._on_group_selected

        # 分隔线
        ttk.Separator(content, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=3)

        # 右侧数据表格
        self.data_table = DataTable(content)
        self.data_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 底部：Hex 命令 + 状态
        self._build_bottom_row(main)
        # 状态栏
        self._build_status_bar()

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        fm = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=fm)
        fm.add_command(label="🔍 数据监控", command=self.open_data_monitor)
        fm.add_separator()
        fm.add_command(label="退出", command=self.on_closing)

    def _build_serial_row(self, parent):
        """串口配置行"""
        frame = ttk.LabelFrame(parent, text="串口", padding=3)
        frame.pack(fill=tk.X, pady=(0, 3))

        ttk.Label(frame, text="端口:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            frame, textvariable=self.port_var, width=35, state="readonly"
        )
        self.port_combo.pack(side=tk.LEFT, padx=2)
        ttk.Button(frame, text="刷新", command=self.refresh_ports, width=5).pack(
            side=tk.LEFT
        )

        ttk.Label(frame, text="波特率:").pack(side=tk.LEFT, padx=(10, 2))
        self.baud_var = tk.StringVar(value="9600")
        ttk.Combobox(
            frame,
            textvariable=self.baud_var,
            width=7,
            state="readonly",
            values=[
                "1200",
                "2400",
                "4800",
                "9600",
                "19200",
                "38400",
                "57600",
                "115200",
            ],
        ).pack(side=tk.LEFT)

        self.open_btn = ttk.Button(
            frame, text="打开串口", command=self.toggle_serial, width=10
        )
        self.open_btn.pack(side=tk.LEFT, padx=(12, 5))
        self.port_status_lbl = ttk.Label(frame, text="🔴", foreground="red")
        self.port_status_lbl.pack(side=tk.LEFT)

    def _build_config_row(self, parent):
        """配置 + 轮循控制行"""
        frame = ttk.LabelFrame(parent, text="配置与轮循", padding=3)
        frame.pack(fill=tk.X, pady=(0, 3))

        ttk.Button(frame, text="📂 加载配置", command=self.load_config_dialog).pack(
            side=tk.LEFT
        )

        self.config_lbl = ttk.Label(frame, text="未加载配置", foreground="gray")
        self.config_lbl.pack(side=tk.LEFT, padx=5)

        ttk.Separator(frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(frame, text="从站ID:").pack(side=tk.LEFT)
        self.poll_slave_var = tk.StringVar(value="1")
        ttk.Entry(frame, textvariable=self.poll_slave_var, width=4).pack(
            side=tk.LEFT, padx=1
        )

        self.start_btn = ttk.Button(
            frame, text="▶ 启动", command=self.start_polling, width=8
        )
        self.start_btn.pack(side=tk.LEFT, padx=(10, 2))
        self.stop_btn = ttk.Button(
            frame, text="⏹ 停止", command=self.stop_polling, width=8, state="disabled"
        )
        self.stop_btn.pack(side=tk.LEFT)

        self.poll_status_lbl = ttk.Label(frame, text="● 停止", foreground="red")
        self.poll_status_lbl.pack(side=tk.LEFT, padx=8)

        self.stats_lbl = ttk.Label(
            frame, text="轮询:0 成功:0 失败:0", foreground="gray"
        )
        self.stats_lbl.pack(side=tk.LEFT, padx=5)

        # 写入/读取按钮
        self.read_btn = ttk.Button(
            frame,
            text="📖 读取",
            command=lambda: self._read_write_group(silent=False),
            width=8,
            state="disabled",
        )
        self.read_btn.pack(side=tk.RIGHT, padx=2)
        self.write_btn = ttk.Button(
            frame, text="✏ 写入", command=self._do_write, width=8, state="disabled"
        )
        self.write_btn.pack(side=tk.RIGHT, padx=2)

    def _build_bottom_row(self, parent):
        """底部：状态日志"""
        self.msg_var = tk.StringVar(value="就绪")
        ttk.Label(
            parent, textvariable=self.msg_var, foreground="gray", font=("Consolas", 9)
        ).pack(side=tk.BOTTOM, fill=tk.X, pady=(1, 0), anchor=tk.W)

    def _build_status_bar(self):
        """底部状态栏"""
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=(0, 3))
        self.port_bar = ttk.Label(
            bar, text="串口: 未连接", foreground="gray", font=("", 8)
        )
        self.port_bar.pack(side=tk.LEFT, padx=5)
        self.poll_bar = ttk.Label(
            bar, text="轮循: 停止", foreground="gray", font=("", 8)
        )
        self.poll_bar.pack(side=tk.LEFT, padx=15)
        self.cfg_bar = ttk.Label(
            bar, text="配置: 未加载", foreground="gray", font=("", 8)
        )
        self.cfg_bar.pack(side=tk.LEFT, padx=5)

    # ==================== 串口 ====================

    def refresh_ports(self):
        ports = self.serial_mgr.refresh_port_list()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def toggle_serial(self):
        if self.serial_mgr.is_open:
            self.close_serial()
        else:
            self.open_serial()

    def open_serial(self):
        dsp = self.port_var.get()
        if not dsp:
            messagebox.showwarning("", "请选择串口")
            return
        port = self.serial_mgr.parse_port_name(dsp)
        try:
            self.serial_mgr.open(
                port=port,
                baudrate=int(self.baud_var.get()),
                timeout=0.5,
            )
            self._set_msg(f"串口 {port} 已打开")
        except Exception as e:
            messagebox.showerror("串口错误", str(e))

    def close_serial(self):
        self.serial_mgr.close()
        self._set_msg("串口已关闭")
        self._update_ui_state()

    def _on_serial_received(self, frame: bytes, parsed: dict):
        with self._sync_lock:
            self._sync_response = frame
            self._sync_event.set()

    def _on_serial_error(self, msg: str):
        self._set_msg(f"串口错误: {msg}")

    def _on_serial_status_change(self, is_open: bool):
        if is_open:
            self.open_btn.config(text="关闭串口")
            self.port_status_lbl.config(text="🟢", foreground="green")
            self.port_bar.config(text="串口: 已连接", foreground="green")
        else:
            self.open_btn.config(text="打开串口")
            self.port_status_lbl.config(text="🔴", foreground="red")
            self.port_bar.config(text="串口: 未连接", foreground="gray")
        self._update_ui_state()

    # ==================== 配置 ====================

    def load_config_dialog(self):
        path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self._load_config(path)

    def _load_config(self, filepath: str):
        try:
            config = load_config(filepath)
            errs = validate_config(config)
            if errs and not messagebox.askyesno(
                "提示", "\n".join(errs) + "\n\n仍要加载？"
            ):
                return

            # 保存配置
            self.app_configs[filepath] = config
            self.app_config = config
            self.config_filepath = filepath

            name = os.path.basename(filepath)
            self.config_lbl.config(text=f"✅ {name}", foreground="green")
            self.cfg_bar.config(text=f"配置: {name}", foreground="green")

            # 同步波特率
            self.baud_var.set(str(config.serial.baudrate))
            self.poll_slave_var.set(str(config.slave_id))

            # 添加到导航树
            self.group_nav.add_config(filepath, config)
            self.group_nav.select_first()

            total_points = sum(len(g.points) for g in config.groups)
            self._set_msg(
                f"✅ 配置加载: {name} ({len(config.groups)}组 {total_points}个点位)"
            )
            self._update_ui_state()

        except Exception as e:
            messagebox.showerror("错误", f"加载失败: {e}")

    # ==================== 轮循 ====================

    def start_polling(self):
        if not self.serial_mgr.is_open:
            messagebox.showwarning("", "请先打开串口")
            return
        if not self.app_config:
            messagebox.showwarning("", "请先加载配置")
            return

        try:
            self.app_config.slave_id = int(self.poll_slave_var.get())
        except ValueError:
            messagebox.showerror("", "从站ID须为数字")
            return

        # 获取当前选中的组，只轮循该组
        selected = self.group_nav.get_selected()
        if selected:
            sel_path, sel_group = selected
            config = self.app_configs.get(sel_path, self.app_config)
            target = sel_group
            # 检查是否是读组（03/04），写组不能轮循
            for g in config.groups:
                if g.name == sel_group and g.function_code not in (0x03, 0x04):
                    messagebox.showwarning(
                        "",
                        f"组 '{sel_group}' 不是读组（FC=0x{g.function_code:02X}），无法轮循",
                    )
                    return
            self.polling_engine.load_config(config)
        else:
            target = None
            self.polling_engine.load_config(self.app_config)

        if self.polling_engine.start(target_group=target):
            self._set_msg(f"▶ 轮循已启动: {target or '全部读组'}")
            self._update_ui_state()
        else:
            messagebox.showwarning("", "启动失败，检查配置中是否有启用的组")

    def stop_polling(self):
        self.polling_engine.stop()
        self._set_msg("⏹ 轮循已停止")
        self._update_ui_state()

    # ---------- 轮循回调 ----------

    def _send_poll_request(self, frame: bytes) -> Optional[bytes]:
        if not self.serial_mgr.is_open:
            return None
        with self._sync_lock:
            self._sync_response = None
            self._sync_event.clear()
        if not self.serial_mgr.send(frame):
            return None
        if self._sync_event.wait(timeout=1.0):
            with self._sync_lock:
                r = self._sync_response
                self._sync_response = None
            return r
        return None

    def _on_poll_result(self, result: GroupPollResult):
        self.root.after(0, lambda: self.data_table.update_from_result(result))
        stats = self.polling_engine.get_stats()
        txt = f"轮询:{stats['total']} 成功:{stats['success']} 失败:{stats['failed']}"
        self.root.after(0, lambda: self.stats_lbl.config(text=txt))
        if result.success:
            self._set_msg(f"✅ {result.group_name}: {len(result.points)}个点更新")
        else:
            self._set_msg(f"❌ {result.group_name}: {result.error_message}")

    def _on_polling_status_change(self, running: bool, msg: str):
        if running:
            self.poll_status_lbl.config(text="● 运行中", foreground="green")
            self.poll_bar.config(text="轮循: 运行中", foreground="green")
        else:
            self.poll_status_lbl.config(text="● 停止", foreground="red")
            self.poll_bar.config(text="轮循: 停止", foreground="gray")
        self._update_ui_state()

    # ==================== 数据监控 ====================

    def open_data_monitor(self):
        """打开底层数据监控窗口"""
        self.data_monitor.show()
        self._set_msg("🔍 数据监控窗口已打开")

    # ==================== 写入/读取操作 ====================

    def _read_write_group(self, silent=False):
        """读取写组的当前值（使用 03 读保持寄存器）
        Args:
            silent: True=自动触发，串口未开时静默跳过；False=按钮点击，弹窗提示
        """
        if not self.serial_mgr.is_open:
            if not silent:
                messagebox.showwarning("", "请先打开串口")
            return
        if not self.app_config or not self._current_write_group:
            return
        group = None
        for g in self.app_config.groups:
            if g.name == self._current_write_group:
                group = g
                break
        if not group or group.function_code != 0x10:
            return

        def _do():
            try:
                slave_id = int(self.poll_slave_var.get())
                frame = build_modbus_frame(
                    slave_id, 0x03, [group.start_address, group.quantity]
                )
                frame = add_crc(frame)
                hex_req = bytes_to_hex_str(frame)
                self.root.after(
                    0, lambda: self._set_msg(f"📖 读取 {group.name}: {hex_req}")
                )

                response = self._send_poll_request(frame)
                if response is None:
                    self.root.after(
                        0, lambda: self._set_msg(f"⏱ 读取超时: {group.name}")
                    )
                    return

                parsed = parse_modbus_response(response)
                if "error" in parsed:
                    self.root.after(
                        0, lambda: self._set_msg(f"❌ 读取错误: {parsed['error']}")
                    )
                    return

                registers = parsed.get("registers", [])
                for point in group.points:
                    if point.register_index >= len(registers):
                        continue
                    raw_val = registers[point.register_index]
                    converted = convert_register_value(
                        raw_val,
                        signed=point.signed,
                        scale=point.scale,
                        offset=point.offset,
                    )
                    if isinstance(converted, float):
                        display = (
                            f"{converted:.{point.decimals}f}"
                            if point.decimals
                            else f"{converted:.1f}"
                        )
                    else:
                        display = str(converted)
                    raw_hex = f"0x{raw_val:04X}"
                    self.root.after(
                        0,
                        lambda pn=point.name, ri=point.register_index, d=display, rh=raw_hex: (
                            self._update_write_row_from_read(pn, ri, d, rh)
                        ),
                    )

                self.root.after(0, lambda: self._set_msg(f"✅ 读取完成: {group.name}"))
            except Exception as ex:
                self.root.after(0, lambda e=ex: self._set_msg(f"❌ 读取异常: {e}"))

        threading.Thread(target=_do, daemon=True).start()

    def _update_write_row_from_read(
        self, point_name: str, register_index: int, display_val: str, raw_hex: str
    ):
        """用读取到的值更新数据表的写点位（不覆盖用户已编辑的）"""
        item_id = f"point_{point_name}_{register_index}"
        if not self.data_table.tree.exists(item_id):
            return
        cur = list(self.data_table.tree.item(item_id, "values"))
        if len(cur) < 10:
            return
        cur[7] = raw_hex  # raw_value column
        # 只在用户尚未编辑时更新转换值
        if cur[8] in ("---", "等待", "双击编辑", ""):
            cur[8] = display_val
        self.data_table.tree.item(item_id, values=tuple(cur))

    def _do_write(self):
        """执行写入：从数据表读取编辑的值，构建 0x10 帧发送"""
        if not self.serial_mgr.is_open:
            messagebox.showwarning("", "请先打开串口")
            return
        if not self.app_config or not self._current_write_group:
            return

        group = None
        for g in self.app_config.groups:
            if g.name == self._current_write_group:
                group = g
                break
        if not group or group.function_code != 0x10:
            return

        # 从数据表获取用户编辑的值
        write_vals = self.data_table.get_write_values()
        if not write_vals:
            messagebox.showinfo("", "请先在数据表的「转换值」列双击输入要写入的值")
            return

        # 收集寄存器字节
        register_values = []
        for point in group.points:
            raw_str = write_vals.get(point.name, "0")
            try:
                # 用户输入的是显示值（已转换），需反算回原始寄存器值
                display_val = float(raw_str)
                raw = int((display_val - point.offset) / point.scale)
                if point.signed:
                    if raw < 0:
                        raw += 0x10000
                    elif raw >= 0x8000:
                        raw -= 0x10000
                raw = raw & 0xFFFF
            except (ValueError, ZeroDivisionError):
                messagebox.showerror("", f"'{point.name}' 的值 '{raw_str}' 无效")
                return
            register_values.extend([(raw >> 8) & 0xFF, raw & 0xFF])

        if not register_values:
            return

        try:
            slave_id = int(self.poll_slave_var.get())
            frame = build_modbus_frame(
                slave_id, 0x10, [group.start_address, group.quantity, register_values]
            )
            frame = add_crc(frame)
            hex_str = bytes_to_hex_str(frame)
            self.serial_mgr.send(frame)
            self._set_msg(f"✏ 写入 {group.name}: {hex_str}")
            self.data_table.clear_write_tags()
        except Exception as e:
            messagebox.showerror("", f"写入失败: {e}")

    # ==================== 组导航回调 ====================

    def _on_group_selected(self, filepath: str, group_name: str):
        """组导航选中某组时，更新数据表"""
        config = self.app_configs.get(filepath)
        if not config:
            return
        # 切换到该配置
        self.app_config = config
        self.config_filepath = filepath
        name = os.path.basename(filepath)
        self.config_lbl.config(text=f"✅ {name}", foreground="green")
        self.cfg_bar.config(text=f"配置: {name}", foreground="green")
        self.poll_slave_var.set(str(config.slave_id))

        self.data_table.show_group(config, group_name)

        # 查找该组配置
        selected_group = None
        for g in config.groups:
            if g.name == group_name:
                selected_group = g
                break

        # 检查是否是写组 (FC=0x10)
        self._current_write_group = None
        self.write_btn.config(state="disabled")
        self.read_btn.config(state="disabled")

        if selected_group and selected_group.function_code == 0x10:
            self._current_write_group = group_name
            self.write_btn.config(state="normal")
            self.read_btn.config(state="normal")
            # 自动读取当前值（静默模式）
            self.root.after(100, lambda: self._read_write_group(silent=True))
            # 如果轮循正在运行，暂停轮循
            if self.polling_engine.is_running:
                self.polling_engine.stop()
                self._set_msg("⏸ 写组模式，轮循已暂停")
        elif selected_group and self.polling_engine.is_running:
            # 切换到另一个读组，更新轮循目标
            self.polling_engine.set_target_group(group_name)
            self._set_msg(f"▶ 轮循切换到组: {group_name}")
        else:
            self._set_msg(f"切换到组: {group_name}")

        self._update_ui_state()

    # ==================== 工具 ====================

    def _set_msg(self, text: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.msg_var.set(f"[{ts}] {text}")

    def _update_ui_state(self):
        is_open = self.serial_mgr.is_open
        is_poll = self.polling_engine.is_running
        has_cfg = self.app_config is not None

        self.start_btn.config(
            state="normal" if (is_open and has_cfg and not is_poll) else "disabled"
        )
        self.stop_btn.config(state="normal" if is_poll else "disabled")
        # 写按钮状态在 _show/_hide_write_panel 中管理

    def on_closing(self):
        self.polling_engine.stop()
        self.serial_mgr.close()
        self.root.destroy()
