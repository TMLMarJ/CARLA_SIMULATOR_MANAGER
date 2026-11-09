from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

import messagebox

from scenarios.paths import (
    UnknownScenarioExtensionError,
    normalize_scenario_path,
    scenario_path_for_name,
    sanitize_scenario_filename,
)
from simulation.manager import SimulationManager

class ScenarioSaveFrame(ttk.LabelFrame):
    def __init__(self, parent, manager):
        super().__init__(
            parent,
            text="Scenario Management",
            style="Section.TLabelframe",
            padding=(12, 8),
        )
        self.manager = manager
        self.connected = False
        self.pending = False
        self.name_var = tk.StringVar(value="Scenario")
        self.path_var = tk.StringVar(value="")
        self.format_var = tk.StringVar(value="JSON")
        self.clear_mode_var = tk.StringVar(value="Managed Actors Only")
        self.status_var = tk.StringVar(
            value="Connect to CARLA to manage scenarios."
        )
        self._updating_path = False
        self._path_filename_manual = False
        self._overwrite_confirmed_path = None
        self.name_var.trace_add("write", self._on_name_changed)
        self.path_var.trace_add("write", self._on_path_changed)
        self._create_widgets()

    def _create_widgets(self):
        self.grid_columnconfigure(3, weight=1)
        ttk.Label(self, text="Name:").grid(
            row=0, column=0, padx=(0, 5), pady=4, sticky="w"
        )
        self.name_entry = ttk.Entry(
            self,
            textvariable=self.name_var,
            width=24,
        )
        self.name_entry.grid(row=0, column=1, padx=(0, 12), pady=4, sticky="w")
        ttk.Label(self, text="Format:").grid(
            row=0, column=2, padx=(0, 5), pady=4, sticky="w"
        )
        self.format_combo = ttk.Combobox(
            self,
            textvariable=self.format_var,
            values=("JSON", "YAML"),
            state="readonly",
            width=8,
        )
        self.format_combo.grid(row=0, column=3, pady=4, sticky="w")
        self.format_combo.bind(
            "<<ComboboxSelected>>",
            self._on_format_changed,
        )

        ttk.Label(self, text="File:").grid(
            row=1, column=0, padx=(0, 5), pady=4, sticky="w"
        )
        self.path_entry = ttk.Entry(self, textvariable=self.path_var)
        self.path_entry.grid(
            row=1,
            column=1,
            columnspan=3,
            padx=(0, 8),
            pady=4,
            sticky="ew",
        )
        self.browse_button = ttk.Button(
            self,
            text="Browse...",
            command=self.browse,
        )
        self.browse_button.grid(row=1, column=4, padx=(0, 8), pady=4)
        self.save_button = ttk.Button(
            self,
            text="Save Scenario",
            command=self.save,
            state="disabled",
        )
        self.save_button.grid(row=1, column=5, pady=4)
        ttk.Label(
            self,
            textvariable=self.status_var,
            anchor="w",
        ).grid(
            row=2,
            column=0,
            columnspan=6,
            pady=(4, 0),
            sticky="ew",
        )

        ttk.Separator(self, orient="horizontal").grid(
            row=3,
            column=0,
            columnspan=6,
            sticky="ew",
            pady=(10, 6),
        )
        ttk.Label(self, text="Clear mode:").grid(
            row=4,
            column=0,
            padx=(0, 5),
            pady=4,
            sticky="w",
        )
        self.clear_mode_combo = ttk.Combobox(
            self,
            textvariable=self.clear_mode_var,
            values=("Managed Actors Only", "Entire World"),
            state="readonly",
            width=22,
        )
        self.clear_mode_combo.grid(
            row=4,
            column=1,
            columnspan=2,
            padx=(0, 12),
            pady=4,
            sticky="w",
        )
        self.clear_button = ttk.Button(
            self,
            text="Clear World",
            command=self.clear_world,
            state="disabled",
        )
        self.clear_button.grid(
            row=4,
            column=5,
            pady=4,
            sticky="e",
        )
        self.load_button = ttk.Button(
            self,
            text="Load Scenario",
            command=self.load_scenario,
            state="disabled",
        )
        self.load_button.grid(
            row=4,
            column=4,
            padx=(0, 8),
            pady=4,
            sticky="e",
        )
        self._update_widget_state()

    def browse(self):
        file_format = self.format_var.get().lower()
        extension = ".json" if file_format == "json" else ".yaml"
        filetypes = (
            (("JSON scenario", "*.json"), ("All files", "*.*"))
            if file_format == "json"
            else (("YAML scenario", "*.yaml *.yml"), ("All files", "*.*"))
        )
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save CARLA scenario",
            defaultextension=extension,
            initialfile=(
                f"{sanitize_scenario_filename(self.name_var.get())}"
                f"{extension}"
            ),
            filetypes=filetypes,
        )
        if path:
            if self._set_normalized_path(path, confirm_unknown=True):
                self._path_filename_manual = (
                    Path(self.path_var.get()).stem.casefold()
                    != sanitize_scenario_filename(
                        self.name_var.get()
                    ).casefold()
                )
                selected = Path(self.path_var.get())
                self._overwrite_confirmed_path = (
                    self._path_key(selected) if selected.exists() else None
                )

    def _on_name_changed(self, *_args):
        if self._path_filename_manual:
            return
        target = scenario_path_for_name(
            self.path_var.get(),
            self.name_var.get(),
            self.format_var.get(),
        )
        self._set_path_value(target, manual_filename=False)

    def _on_path_changed(self, *_args):
        if self._updating_path:
            return
        self._overwrite_confirmed_path = None
        path = self.path_var.get().strip()
        if not path:
            self._path_filename_manual = False
            return
        expected = sanitize_scenario_filename(self.name_var.get())
        self._path_filename_manual = (
            Path(path).stem.casefold() != expected.casefold()
        )

    def _set_path_value(self, path, manual_filename=None):
        self._overwrite_confirmed_path = None
        self._updating_path = True
        try:
            self.path_var.set(str(path))
        finally:
            self._updating_path = False
        if manual_filename is not None:
            self._path_filename_manual = bool(manual_filename)

    def _on_format_changed(self, event=None):
        path = self.path_var.get().strip()
        if path:
            self._set_normalized_path(path, confirm_unknown=False)

    def _set_normalized_path(self, path, confirm_unknown):
        file_format = self.format_var.get().strip().lower()
        try:
            target = normalize_scenario_path(path, file_format)
        except UnknownScenarioExtensionError as error:
            if not confirm_unknown:
                messagebox.showwarning(
                    "Scenario file extension",
                    f"{error}\nThe path has not been changed.",
                )
                return False
            if not messagebox.askyesno(
                "Scenario file extension",
                f"{error}\n\nUse the suggested file name?",
            ):
                return False
            target = normalize_scenario_path(
                path,
                file_format,
                allow_unknown=True,
            )
        manual_filename = getattr(self, "_path_filename_manual", False)
        self._set_path_value(target, manual_filename=manual_filename)
        return True

    def save(self):
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        file_format = self.format_var.get().strip().lower()
        if not name:
            messagebox.showerror(
                "Save Scenario",
                "Scenario name cannot be empty.",
            )
            return
        if not path:
            self.browse()
            path = self.path_var.get().strip()
            if not path:
                return
        if not self._set_normalized_path(path, confirm_unknown=True):
            return
        target = Path(self.path_var.get())
        target_key = self._path_key(target)
        if (
            target.exists()
            and target_key != self._overwrite_confirmed_path
            and not messagebox.askyesno(
                "Overwrite Scenario",
                f"The scenario file already exists:\n{target}\n\n"
                "Overwrite it?",
            )
        ):
            return
        if target.exists():
            self._overwrite_confirmed_path = target_key
        self.manager.save_scenario(
            name,
            self.path_var.get(),
            file_format,
        )

    @staticmethod
    def _path_key(path):
        return str(Path(path).absolute()).casefold()

    def clear_world(self):
        self.manager.prepare_clear_world(self.clear_mode_var.get())

    def load_scenario(self):
        self.manager.load_scenario()

    def set_connected(self, connected):
        self.connected = bool(connected)
        if not self.connected:
            self.pending = False
            self.status_var.set("Connect to CARLA to manage scenarios.")
        self._update_widget_state()

    def set_pending(self):
        self.pending = True
        self.status_var.set("Capturing and saving scenario...")
        self._update_widget_state()

    def set_clear_pending(self, status="Preparing Clear World preflight..."):
        self.pending = True
        self.status_var.set(status)
        self._update_widget_state()

    def set_load_pending(self, status="Validating scenario..."):
        self.pending = True
        self.status_var.set(status)
        self._update_widget_state()

    def show_load_preflight(self, plan):
        self.status_var.set(
            f"Load preflight {plan.get('status', 'unknown')}: "
            f"{plan.get('actor_count', 0)} actor(s); waiting for confirmation."
        )

    def show_load_cancelled(self):
        self.pending = False
        self.status_var.set("Scenario Load cancelled; the world was not changed.")
        self._update_widget_state()

    def show_load_report(self, report):
        self.pending = False
        self.status_var.set(
            f"Scenario Load {report.get('status', 'failed')}: restored "
            f"{report.get('restored', 0)}/{report.get('planned', 0)}, "
            f"skipped {report.get('skipped', 0)}, failed "
            f"{report.get('failed', 0)}."
        )
        self._update_widget_state()

    def show_load_error(self, message):
        self.pending = False
        self.status_var.set(f"Scenario Load failed: {message}")
        self._update_widget_state()

    def show_clear_preflight(self, plan):
        self.status_var.set(
            f"Preflight ready: {plan.get('planned_count', 0)} actor(s); "
            "waiting for confirmation."
        )

    def show_clear_report(self, report):
        self.pending = False
        status = report.get("status", "failed")
        if status == "cancelled":
            self.status_var.set("Clear World cancelled.")
        elif report.get("planned_count", 0) == 0:
            self.status_var.set("World already clear.")
        else:
            self.status_var.set(
                f"Clear World {status}: removed "
                f"{report.get('removed', 0)}, already missing "
                f"{report.get('already_missing', 0)}, failed "
                f"{report.get('failed', 0)}."
            )
        self._update_widget_state()

    def show_clear_error(self, message):
        self.pending = False
        self.status_var.set(f"Clear World failed: {message}")
        self._update_widget_state()

    def show_result(self, report):
        self.pending = False
        if report.get("path"):
            self._set_path_value(
                report["path"],
                manual_filename=self._path_filename_manual,
            )
        skipped = sum(
            report.get(field, 0)
            for field in (
                "skipped_runtime_only",
                "skipped_unrestorable",
                "skipped_external",
                "skipped_unsupported",
            )
        )
        self.status_var.set(
            f"Saved {report.get('saved', 0)} actors "
            f"({report.get('partial', 0)} partial), skipped "
            f"{skipped}."
        )
        self._update_widget_state()

    def show_error(self, message):
        self.pending = False
        self.status_var.set(f"Save failed: {message}")
        self._update_widget_state()

    def _update_widget_state(self):
        enabled = self.connected and not self.pending
        state = "normal" if enabled else "disabled"
        self.name_entry.configure(state=state)
        self.path_entry.configure(state=state)
        self.browse_button.configure(state=state)
        self.save_button.configure(state=state)
        self.clear_button.configure(state=state)
        self.load_button.configure(state=state)
        self.format_combo.configure(
            state="readonly" if enabled else "disabled"
        )
        self.clear_mode_combo.configure(
            state="readonly" if enabled else "disabled"
        )

class SimulationControlFrame(ttk.Frame):
    def __init__(self, parent, manager):
        super().__init__(parent)
        self.manager = manager
        self.connected = False
        self.pending = False
        self.control_pending = False
        self.external_busy = False
        self.advanced_visible = False
        self.control_state = SimulationManager.DISCONNECTED
        self.control_synchronous_mode = False
        self.last_control_error = None

        self.status_var = tk.StringVar(value="Disconnected")
        self.mode_var = tk.StringVar(value="")
        self.fixed_delta_var = tk.StringVar(value="")
        self.no_rendering_var = tk.BooleanVar(value=False)
        self.substepping_var = tk.BooleanVar(value=False)
        self.max_substep_delta_var = tk.StringVar(value="")
        self.max_substeps_var = tk.StringVar(value="")
        self.tick_controller_var = tk.StringVar(value="External")
        self.control_status_var = tk.StringVar(value="Disconnected")
        self.frame_status_var = tk.StringVar(value="Last observed frame: -")
        self.step_count_var = tk.StringVar(value="10")
        self.setting_widgets = []

        self._create_widgets()
        self._update_widget_states()

    def _create_widgets(self):
        ttk.Label(self, text="Simulation Control").grid(
            row=0,
            column=0,
            columnspan=4,
            sticky="w",
            padx=8,
            pady=(10, 4),
        )
        ttk.Label(self, text="Status:").grid(
            row=0,
            column=4,
            sticky="e",
            padx=(8, 4),
            pady=(10, 4),
        )
        ttk.Label(self, textvariable=self.status_var).grid(
            row=0,
            column=5,
            sticky="w",
            padx=(0, 8),
            pady=(10, 4),
        )

        settings_frame = ttk.LabelFrame(
            self,
            text="World Settings",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        settings_frame.grid(
            row=1,
            column=0,
            columnspan=6,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )

        ttk.Label(settings_frame, text="Simulation mode").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.mode_combobox = ttk.Combobox(
            settings_frame,
            textvariable=self.mode_var,
            values=("Synchronous", "Asynchronous"),
            state="disabled",
            width=15,
        )
        self.mode_combobox.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.mode_combobox, "readonly"))

        ttk.Label(settings_frame, text="Fixed Delta Seconds (0 = variable)").grid(
            row=0,
            column=2,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.fixed_delta_entry = ttk.Entry(
            settings_frame,
            textvariable=self.fixed_delta_var,
            width=14,
        )
        self.fixed_delta_entry.grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.fixed_delta_entry, "normal"))

        self.no_rendering_checkbutton = ttk.Checkbutton(
            settings_frame,
            text="No Rendering Mode",
            variable=self.no_rendering_var,
        )
        self.no_rendering_checkbutton.grid(
            row=0,
            column=4,
            sticky="w",
            padx=(4, 12),
            pady=4,
        )
        self.setting_widgets.append((self.no_rendering_checkbutton, "normal"))

        settings_actions = ttk.Frame(settings_frame)
        settings_actions.grid(
            row=1,
            column=0,
            columnspan=5,
            sticky="ew",
            padx=4,
            pady=(4, 2),
        )
        settings_actions.grid_columnconfigure(0, weight=1)

        self.advanced_button = ttk.Button(
            settings_actions,
            text="Advanced Settings ↓",
            command=self._toggle_advanced,
        )
        self.advanced_button.grid(
            row=0,
            column=0,
            sticky="w",
        )

        self.advanced_frame = ttk.Frame(settings_frame)
        self.substepping_checkbutton = ttk.Checkbutton(
            self.advanced_frame,
            text="Enable Substepping",
            variable=self.substepping_var,
        )
        self.substepping_checkbutton.grid(
            row=0,
            column=0,
            sticky="w",
            padx=(4, 12),
            pady=4,
        )
        self.setting_widgets.append((self.substepping_checkbutton, "normal"))

        ttk.Label(self.advanced_frame, text="Max Substep Delta Time").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.max_substep_delta_entry = ttk.Entry(
            self.advanced_frame,
            textvariable=self.max_substep_delta_var,
            width=14,
        )
        self.max_substep_delta_entry.grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.max_substep_delta_entry, "normal"))

        ttk.Label(self.advanced_frame, text="Max Substeps").grid(
            row=0,
            column=3,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.max_substeps_entry = ttk.Entry(
            self.advanced_frame,
            textvariable=self.max_substeps_var,
            width=10,
        )
        self.max_substeps_entry.grid(
            row=0,
            column=4,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.max_substeps_entry, "normal"))

        self.apply_button = ttk.Button(
            settings_actions,
            text="Apply Settings",
            command=self._apply_settings,
        )
        self.apply_button.grid(
            row=0,
            column=1,
            sticky="e",
            padx=(12, 8),
        )

        for column in range(5):
            settings_frame.grid_columnconfigure(column, weight=1)
            self.advanced_frame.grid_columnconfigure(column, weight=1)

        control_frame = ttk.LabelFrame(
            self,
            text="Tick Control",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        control_frame.grid(
            row=2,
            column=0,
            columnspan=6,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )

        ttk.Label(control_frame, text="Tick Controller").grid(
            row=0, column=0, sticky="w", padx=(4, 6), pady=4
        )
        self.tick_controller_combobox = ttk.Combobox(
            control_frame,
            textvariable=self.tick_controller_var,
            values=("GUI", "External"),
            state="disabled",
            width=12,
        )
        self.tick_controller_combobox.grid(
            row=0, column=1, sticky="w", padx=(0, 12), pady=4
        )
        self.tick_controller_combobox.bind(
            "<<ComboboxSelected>>",
            self._change_tick_controller,
        )

        ttk.Label(control_frame, text="Control status:").grid(
            row=0, column=2, sticky="e", padx=(4, 6), pady=4
        )
        ttk.Label(control_frame, textvariable=self.control_status_var).grid(
            row=0, column=3, sticky="w", padx=(0, 12), pady=4
        )
        ttk.Label(control_frame, textvariable=self.frame_status_var).grid(
            row=0, column=4, columnspan=2, sticky="e", padx=(4, 12), pady=4
        )

        self.run_button = ttk.Button(
            control_frame, text="Run", command=self.manager.run_simulation
        )
        self.run_button.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self.pause_button = ttk.Button(
            control_frame, text="Pause", command=self.manager.pause_simulation
        )
        self.pause_button.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.step_button = ttk.Button(
            control_frame, text="Step", command=self._step_once
        )
        self.step_button.grid(row=1, column=2, sticky="ew", padx=4, pady=4)

        ttk.Label(control_frame, text="Step Count").grid(
            row=1, column=3, sticky="e", padx=(4, 6), pady=4
        )
        self.step_count_entry = ttk.Entry(
            control_frame, textvariable=self.step_count_var, width=10
        )
        self.step_count_entry.grid(
            row=1, column=4, sticky="ew", padx=(0, 4), pady=4
        )
        self.step_n_button = ttk.Button(
            control_frame, text="Step N", command=self._step_many
        )
        self.step_n_button.grid(row=1, column=5, sticky="ew", padx=4, pady=4)

        ttk.Label(
            control_frame,
            text=(
                "GUI control is cooperative: another CARLA client may also "
                "issue ticks. Paused only stops ticks initiated by this GUI."
            ),
            wraplength=900,
        ).grid(
            row=2,
            column=0,
            columnspan=6,
            sticky="w",
            padx=4,
            pady=(4, 2),
        )
        for column in range(6):
            control_frame.grid_columnconfigure(column, weight=1)

        self.scenario_frame = ScenarioSaveFrame(self, self.manager)
        self.scenario_frame.grid(
            row=3,
            column=0,
            columnspan=6,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )
        self.grid_columnconfigure(0, weight=1)

    def _toggle_advanced(self):
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid(
                row=2,
                column=0,
                columnspan=5,
                sticky="ew",
                padx=0,
                pady=(0, 2),
            )
            self.advanced_button.configure(text="Advanced Settings ↑")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_button.configure(text="Advanced Settings ↓")

    def _apply_settings(self):
        try:
            settings = self.get_proposed_settings()
        except ValueError as error:
            messagebox.showerror("Invalid Simulation Settings", str(error))
            return
        self.manager.apply_simulation_settings(settings)

    def _change_tick_controller(self, event=None):
        controller = self.tick_controller_var.get().lower()
        self.manager.set_tick_controller(controller)

    def _step_once(self):
        self.manager.step_simulation(1)

    def _step_many(self):
        try:
            count = int(self.step_count_var.get().strip())
            if count <= 0:
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Invalid Step Count",
                "Step Count must be a positive integer.",
            )
            return
        self.manager.step_simulation(count)

    def get_proposed_settings(self):
        mode = self.mode_var.get()
        if mode not in ("Synchronous", "Asynchronous"):
            raise ValueError("Select Synchronous or Asynchronous mode.")

        try:
            fixed_delta_text = self.fixed_delta_var.get().strip()
            fixed_delta_seconds = (
                float(fixed_delta_text) if fixed_delta_text else None
            )
            max_substep_delta_time = float(
                self.max_substep_delta_var.get().strip()
            )
            max_substeps = int(self.max_substeps_var.get().strip())
        except (tk.TclError, ValueError):
            raise ValueError("Simulation settings must contain valid numbers.")

        settings = {
            "synchronous_mode": mode == "Synchronous",
            "fixed_delta_seconds": fixed_delta_seconds,
            "no_rendering_mode": bool(self.no_rendering_var.get()),
            "substepping": bool(self.substepping_var.get()),
            "max_substep_delta_time": max_substep_delta_time,
            "max_substeps": max_substeps,
        }
        SimulationManager.validate_settings(settings)
        return settings

    def set_connected(self, connected, settings=None, control_state=None):
        self.connected = connected
        self.pending = False
        self.control_pending = False
        self.external_busy = False
        if connected and settings is not None:
            self.load_settings(settings, "Settings loaded")
        elif connected:
            self.status_var.set("Loading settings...")
        else:
            self.status_var.set("Disconnected")
            self._clear_settings()
        if connected and control_state is not None:
            self.update_control_state(control_state)
        elif not connected:
            self.control_state = SimulationManager.DISCONNECTED
            self.control_synchronous_mode = False
            self.control_status_var.set("Disconnected")
            self.frame_status_var.set("Last observed frame: -")
            self.tick_controller_var.set("External")
        self._update_widget_states()

    def set_external_busy(self, busy):
        self.external_busy = busy
        self._update_widget_states()

    def set_pending(self):
        self.pending = True
        self.status_var.set("Applying settings...")
        self._update_widget_states()

    def set_control_pending(self, status):
        self.control_pending = True
        self.control_status_var.set(status)
        self._update_widget_states()

    def update_control_state(self, control_state):
        self.control_pending = False
        self.control_state = control_state["state"]
        self.control_synchronous_mode = control_state["synchronous_mode"]
        controller = control_state["tick_controller"]
        self.tick_controller_var.set(
            "GUI" if controller == SimulationManager.GUI_CONTROLLER else "External"
        )

        status = self.control_state
        remaining_steps = control_state.get("remaining_steps", 0)
        if self.control_state == SimulationManager.SYNC_GUI_STEPPING:
            status = f"{status} ({remaining_steps} remaining)"
        self.control_status_var.set(status)

        last_frame = control_state.get("last_frame")
        self.frame_status_var.set(
            "Last observed frame: -"
            if last_frame is None
            else f"Last observed frame: {last_frame}"
        )

        error = control_state.get("error")
        if error and error != self.last_control_error:
            self.last_control_error = error
            messagebox.showerror(
                "Simulation Tick Error",
                f"The local tick scheduler stopped:\n{error}",
            )
        elif not error:
            self.last_control_error = None
        self._update_widget_states()

    def update_control_observation(self, control_state):
        if self.control_pending or control_state["state"] != self.control_state:
            return
        status = self.control_state
        if self.control_state == SimulationManager.SYNC_GUI_STEPPING:
            status = (
                f"{status} ({control_state.get('remaining_steps', 0)} "
                "remaining)"
            )
        self.control_status_var.set(status)
        last_frame = control_state.get("last_frame")
        self.frame_status_var.set(
            "Last observed frame: -"
            if last_frame is None
            else f"Last observed frame: {last_frame}"
        )

    def show_control_error(self, message):
        self.control_pending = False
        self._update_widget_states()
        messagebox.showerror("Simulation Control Error", message)

    def load_settings(self, settings, status="Settings loaded"):
        self.mode_var.set(
            "Synchronous" if settings["synchronous_mode"] else "Asynchronous"
        )
        fixed_delta_seconds = settings["fixed_delta_seconds"]
        self.fixed_delta_var.set(
            "" if fixed_delta_seconds is None else str(fixed_delta_seconds)
        )
        self.no_rendering_var.set(settings["no_rendering_mode"])
        self.substepping_var.set(settings["substepping"])
        self.max_substep_delta_var.set(settings["max_substep_delta_time"])
        self.max_substeps_var.set(settings["max_substeps"])
        self.pending = False
        self.status_var.set(status)
        self._update_widget_states()

    def show_error(self, message, settings=None):
        if settings is not None:
            self.load_settings(settings, "Apply failed; server values restored")
        else:
            self.pending = False
            self.status_var.set("Settings error")
            self._update_widget_states()
        messagebox.showerror(
            "Simulation Settings Error",
            f"Could not apply or read simulation settings:\n{message}",
        )

    def _clear_settings(self):
        self.mode_var.set("")
        self.fixed_delta_var.set("")
        self.no_rendering_var.set(False)
        self.substepping_var.set(False)
        self.max_substep_delta_var.set("")
        self.max_substeps_var.set("")

    def _update_widget_states(self):
        enabled = self.connected and not self.pending and not self.external_busy
        for widget, enabled_state in self.setting_widgets:
            widget.configure(state=enabled_state if enabled else "disabled")
        self.advanced_button.configure(state="normal" if enabled else "disabled")
        self.apply_button.configure(state="normal" if enabled else "disabled")

        controls_enabled = enabled and not self.control_pending
        controller_state = (
            "readonly"
            if controls_enabled and self.control_synchronous_mode
            else "disabled"
        )
        self.tick_controller_combobox.configure(state=controller_state)

        paused = self.control_state == SimulationManager.SYNC_GUI_PAUSED
        running = self.control_state == SimulationManager.SYNC_GUI_RUNNING
        stepping = self.control_state == SimulationManager.SYNC_GUI_STEPPING
        self.run_button.configure(
            state="normal" if controls_enabled and paused else "disabled"
        )
        self.pause_button.configure(
            state=(
                "normal"
                if controls_enabled and (running or stepping)
                else "disabled"
            ),
            text="Cancel Step N" if stepping else "Pause",
        )
        step_state = "normal" if controls_enabled and paused else "disabled"
        self.step_button.configure(state=step_state)
        self.step_count_entry.configure(state=step_state)
        self.step_n_button.configure(state=step_state)

