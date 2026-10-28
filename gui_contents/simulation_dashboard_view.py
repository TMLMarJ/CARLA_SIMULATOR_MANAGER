import datetime
import tkinter as tk
from tkinter import ttk


class SimulationDashboardFrame(ttk.Frame):
    ACTOR_CATEGORIES = ("Vehicles", "Walkers", "Sensors", "Other")
    OWNERSHIP_COLUMNS = ("GUI Managed", "External", "Internal")
    SENSOR_COLUMNS = ("actor_id", "type_id", "parent_id", "status")

    def __init__(self, parent):
        super().__init__(parent)
        self.context_status = None
        self._last_revision = None
        self._create_variables()
        self._create_widgets()
        self.show_context("Disconnected")

    def _create_variables(self):
        self.simulation_vars = {
            "state": tk.StringVar(value="Disconnected"),
            "frame": tk.StringVar(value="-"),
            "elapsed": tk.StringVar(value="-"),
            "delta": tk.StringVar(value="-"),
            "map": tk.StringVar(value="-"),
            "synchronization": tk.StringVar(value="-"),
            "tick_controller": tk.StringVar(value="-"),
        }
        self.application_vars = {
            "connection": tk.StringVar(value="Disconnected"),
            "thread": tk.StringVar(value="Stopped"),
            "pending": tk.StringVar(value="0"),
            "last_update": tk.StringVar(value="-"),
            "last_error": tk.StringVar(value="-"),
            "viewer": tk.StringVar(value="Closed"),
        }

    def _create_widgets(self):
        ttk.Label(self, text="Simulation Dashboard").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(10, 4)
        )

        status_frame = ttk.LabelFrame(
            self,
            text="Simulation Status",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        status_frame.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8)
        )
        status_fields = (
            ("Simulation State", "state"),
            ("Current Frame", "frame"),
            ("Elapsed Simulation Time", "elapsed"),
            ("Delta Time", "delta"),
            ("Map Name", "map"),
            ("Synchronization Mode", "synchronization"),
            ("Tick Controller", "tick_controller"),
        )
        for index, (label, key) in enumerate(status_fields):
            row = index // 2
            column = (index % 2) * 2
            ttk.Label(status_frame, text=f"{label}:").grid(
                row=row, column=column, sticky="w", padx=(4, 6), pady=3
            )
            value_label = ttk.Label(
                status_frame,
                textvariable=self.simulation_vars[key],
            )
            value_label.grid(
                row=row, column=column + 1, sticky="w", padx=(0, 12), pady=3
            )
            if key == "state":
                self.simulation_state_label = value_label
        status_frame.grid_columnconfigure(1, weight=1)
        status_frame.grid_columnconfigure(3, weight=1)

        actors_frame = ttk.LabelFrame(
            self,
            text="Actors Overview",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        actors_frame.grid(row=2, column=0, sticky="nsew", padx=(8, 4), pady=(0, 8))
        actor_columns = ("type", "total", "gui", "external", "internal")
        self.actor_tree = ttk.Treeview(
            actors_frame,
            columns=actor_columns,
            show="headings",
            height=5,
            selectmode="none",
            style="Treeview",
        )
        headings = ("Type", "Total", "GUI Managed", "External", "Internal")
        widths = (115, 65, 95, 75, 70)
        for column, heading, width in zip(actor_columns, headings, widths):
            self.actor_tree.heading(column, text=heading)
            self.actor_tree.column(
                column,
                width=width,
                minwidth=55,
                anchor="w" if column == "type" else "center",
                stretch=True,
            )
        self.actor_tree.pack(fill="both", expand=True)
        for category in self.ACTOR_CATEGORIES + ("Total",):
            self.actor_tree.insert(
                "", "end", iid=category, values=(category, "-", "-", "-", "-")
            )

        sensors_frame = ttk.LabelFrame(
            self,
            text="Sensors Overview",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        sensors_frame.grid(row=2, column=1, sticky="nsew", padx=(4, 8), pady=(0, 8))
        self.sensor_tree = ttk.Treeview(
            sensors_frame,
            columns=self.SENSOR_COLUMNS,
            show="headings",
            height=6,
            selectmode="none",
            style="Treeview",
        )
        sensor_headings = ("Sensor ID", "Type", "Parent", "Status")
        sensor_widths = (75, 190, 70, 85)
        for column, heading, width in zip(
            self.SENSOR_COLUMNS, sensor_headings, sensor_widths
        ):
            self.sensor_tree.heading(column, text=heading)
            self.sensor_tree.column(
                column,
                width=width,
                minwidth=55,
                anchor="center" if column != "type_id" else "w",
                stretch=True,
            )
        sensor_scrollbar = ttk.Scrollbar(
            sensors_frame, orient="vertical", command=self.sensor_tree.yview
        )
        self.sensor_tree.configure(yscrollcommand=sensor_scrollbar.set)
        self.sensor_tree.grid(row=0, column=0, sticky="nsew")
        sensor_scrollbar.grid(row=0, column=1, sticky="ns")
        sensors_frame.grid_rowconfigure(0, weight=1)
        sensors_frame.grid_columnconfigure(0, weight=1)

        application_frame = ttk.LabelFrame(
            self,
            text="Application Status",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        application_frame.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 10)
        )
        application_fields = (
            ("Connection Status", "connection"),
            ("Controller Thread", "thread"),
            ("Pending Commands", "pending"),
            ("Last Update", "last_update"),
            ("Viewer Status", "viewer"),
            ("Last Error", "last_error"),
        )
        for index, (label, key) in enumerate(application_fields):
            row = index // 2
            column = (index % 2) * 2
            ttk.Label(application_frame, text=f"{label}:").grid(
                row=row, column=column, sticky="nw", padx=(4, 6), pady=3
            )
            value_label = ttk.Label(
                application_frame,
                textvariable=self.application_vars[key],
                wraplength=430 if key == "last_error" else 0,
            )
            value_label.grid(
                row=row, column=column + 1, sticky="w", padx=(0, 12), pady=3
            )
            if key == "connection":
                self.connection_status_label = value_label
            elif key == "thread":
                self.thread_status_label = value_label
        application_frame.grid_columnconfigure(1, weight=1)
        application_frame.grid_columnconfigure(3, weight=1)

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

    def show_context(self, status, error=None):
        self.context_status = status
        self.simulation_vars["state"].set(status)
        self.application_vars["connection"].set(status)
        if status in ("Connecting", "Loading World"):
            self.application_vars["thread"].set("Starting" if status == "Connecting" else "Running")
        elif status in ("Disconnected", "Error"):
            self.application_vars["thread"].set("Stopped")
        if error:
            self.application_vars["last_error"].set(str(error))
        if status in ("Disconnected", "Connecting", "Loading World", "Error"):
            self._clear_world_values()
        self._apply_status_styles(status, self.application_vars["thread"].get())

    def clear_context(self):
        self.context_status = None

    def refresh(self, simulation_snapshot, actor_statistics,
                sensor_statistics, application_status):
        connection_status = application_status.get(
            "connection_status", "Disconnected"
        )
        if self.context_status == "Connecting" and connection_status == "Connected":
            self.context_status = None

        self._update_application(application_status)
        if self.context_status is not None:
            self.show_context(
                self.context_status,
                application_status.get("last_error"),
            )
            return

        if not simulation_snapshot.get("valid") or simulation_snapshot.get("stale"):
            reason = simulation_snapshot.get("stale_reason") or connection_status
            state = "Disconnected" if connection_status == "Disconnected" else reason
            self.simulation_vars["state"].set(state)
            self._clear_world_values(keep_state=True)
            self._clear_actor_table()
            self._update_sensor_table([])
            self._apply_status_styles(state, application_status.get("controller_thread_status"))
            return

        state = simulation_snapshot.get("simulation_control_state") or "Unknown"
        self.simulation_vars["state"].set(self.format_simulation_state(state))
        self.simulation_vars["frame"].set(
            self.format_number(simulation_snapshot.get("frame"), 0)
        )
        self.simulation_vars["elapsed"].set(
            self.format_seconds(simulation_snapshot.get("elapsed_seconds"))
        )
        self.simulation_vars["delta"].set(
            self.format_seconds(simulation_snapshot.get("delta_seconds"), 4)
        )
        self.simulation_vars["map"].set(simulation_snapshot.get("map_name") or "-")
        synchronous = simulation_snapshot.get("synchronous_mode")
        self.simulation_vars["synchronization"].set(
            "Synchronous" if synchronous is True
            else "Asynchronous" if synchronous is False
            else "Unknown"
        )
        controller = simulation_snapshot.get("tick_controller")
        self.simulation_vars["tick_controller"].set(
            "GUI" if controller == "gui"
            else "External" if controller == "external"
            else "-"
        )
        self._update_actor_table(actor_statistics)
        self._update_sensor_table(
            sensor_statistics.get("sensors", [])
            if sensor_statistics.get("valid") and not sensor_statistics.get("stale")
            else []
        )
        self._apply_status_styles(state, application_status.get("controller_thread_status"))

    def _update_application(self, status):
        valid = status.get("valid") and not status.get("stale")
        self.application_vars["connection"].set(
            status.get("connection_status") or "Disconnected"
        )
        self.application_vars["thread"].set(
            status.get("controller_thread_status") or "Stopped"
        )
        self.application_vars["pending"].set(
            str(status.get("pending_commands", 0)) if valid else "-"
        )
        self.application_vars["last_update"].set(
            self.format_timestamp(status.get("last_successful_update"))
        )
        self.application_vars["last_error"].set(status.get("last_error") or "-")
        self.application_vars["viewer"].set(status.get("viewer_status") or "-")

    def _update_actor_table(self, statistics):
        if not statistics.get("valid") or statistics.get("stale"):
            self._clear_actor_table()
            return
        counts = self.actor_rows(statistics)
        for category in self.ACTOR_CATEGORIES + ("Total",):
            row = counts[category]
            self.actor_tree.item(
                category,
                values=(category, row["Total"], row["GUI Managed"],
                        row["External"], row["Internal"]),
            )

    def _clear_actor_table(self):
        for category in self.ACTOR_CATEGORIES + ("Total",):
            self.actor_tree.item(
                category, values=(category, "-", "-", "-", "-")
            )

    def _update_sensor_table(self, sensors):
        expected_ids = set()
        for sensor in sorted(sensors, key=lambda item: int(item["actor_id"])):
            item_id = str(sensor["actor_id"])
            expected_ids.add(item_id)
            parent = sensor.get("parent_id")
            values = (
                sensor["actor_id"],
                sensor.get("type_id") or "-",
                "-" if parent is None else parent,
                sensor.get("status") or "Unknown",
            )
            if self.sensor_tree.exists(item_id):
                self.sensor_tree.item(item_id, values=values)
            else:
                self.sensor_tree.insert("", "end", iid=item_id, values=values)
        for item_id in self.sensor_tree.get_children(""):
            if item_id not in expected_ids:
                self.sensor_tree.delete(item_id)

    def _clear_world_values(self, keep_state=False):
        if not keep_state:
            self.simulation_vars["state"].set(self.context_status or "Disconnected")
        for key in ("frame", "elapsed", "delta", "map", "synchronization", "tick_controller"):
            self.simulation_vars[key].set("-")
        self._clear_actor_table()
        self._update_sensor_table([])

    def _apply_status_styles(self, simulation_state, thread_state):
        state_text = str(simulation_state or "")
        if "Error" in state_text:
            simulation_style = "Status.Error.TLabel"
        elif "Paused" in state_text or "External" in state_text or state_text in ("Connecting", "Loading World"):
            simulation_style = "Status.Warning.TLabel"
        elif "Running" in state_text or "Stepping" in state_text:
            simulation_style = "Status.Success.TLabel"
        else:
            simulation_style = "Status.Muted.TLabel"
        self.simulation_state_label.configure(style=simulation_style)

        connection = self.application_vars["connection"].get()
        connection_style = (
            "Status.Success.TLabel" if connection == "Connected"
            else "Status.Error.TLabel" if connection == "Error"
            else "Status.Warning.TLabel" if connection in ("Connecting", "Loading World")
            else "Status.Muted.TLabel"
        )
        self.connection_status_label.configure(style=connection_style)
        self.thread_status_label.configure(
            style=(
                "Status.Success.TLabel"
                if thread_state == "Running"
                else "Status.Warning.TLabel"
                if thread_state == "Starting"
                else "Status.Muted.TLabel"
            )
        )

    @classmethod
    def actor_rows(cls, statistics):
        rows = {
            category: {
                "Total": 0,
                **{ownership: 0 for ownership in cls.OWNERSHIP_COLUMNS},
            }
            for category in cls.ACTOR_CATEGORIES
        }
        for actor in statistics.get("actors", []):
            category = actor.get("category")
            ownership = actor.get("ownership")
            if category not in rows or ownership not in cls.OWNERSHIP_COLUMNS:
                continue
            rows[category]["Total"] += 1
            rows[category][ownership] += 1
        rows["Total"] = {
            key: sum(row[key] for row in rows.values())
            for key in ("Total",) + cls.OWNERSHIP_COLUMNS
        }
        return rows

    @staticmethod
    def format_simulation_state(state):
        mapping = {
            "Async Running": "Running (Asynchronous)",
            "Sync External": "External Control",
            "Sync GUI Paused": "GUI Paused (local ticks only)",
            "Sync GUI Running": "Running (GUI Control)",
            "Sync GUI Stepping": "Stepping (GUI Control)",
        }
        return mapping.get(state, state)

    @staticmethod
    def format_number(value, decimals):
        if value is None:
            return "-"
        return f"{value:.{decimals}f}"

    @classmethod
    def format_seconds(cls, value, decimals=3):
        formatted = cls.format_number(value, decimals)
        return "-" if formatted == "-" else f"{formatted} s"

    @staticmethod
    def format_timestamp(value):
        if value is None:
            return "-"
        try:
            return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, TypeError, ValueError):
            return "-"
