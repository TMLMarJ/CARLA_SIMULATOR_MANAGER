import tkinter as tk
from typing import List, Any

import carla
from tkinter import ttk
from utilities.functions import filter_carla_id_by_type

AVAILABLE_CLASSES = {
    "vehicle": ["Car", "Bus", "Van", "Truck", "Motorcycle", "Bicycle"],
    "walker": ["Pedestrian"],
    "static": ["Prop"]
}

class ChangeModelTop(tk.Toplevel):
    def __init__(self, parent, actor_type: str, blueprint_lib, on_model_change_callback):
        super().__init__(parent)
        self.title("Change Model")
        self.geometry("420x170")
        self.resizable(False, False)
        self.blueprint_lib = blueprint_lib
        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"
        self.configure(bg=self.default_color)
        self.choose_class = tk.StringVar()
        self.choose_model = tk.StringVar()

        self.actor_type = actor_type
        self.on_model_change_callback = on_model_change_callback

        self.available_classes = AVAILABLE_CLASSES.get(actor_type, [])

        self.create_widgets()

    def create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        class_frame = ttk.Frame(self, style="new.TFrame")
        class_frame.grid(row=0, column=0, sticky="ew", padx=(18, 6), pady=(18, 0))

        model_frame = ttk.Frame(self, style="new.TFrame")
        model_frame.grid(row=0, column=1, sticky="ew", padx=(6, 18), pady=(18, 0))

        class_frame.grid_columnconfigure(0, weight=1)
        model_frame.grid_columnconfigure(0, weight=1)

        # Class
        class_label = ttk.Label(class_frame, text="Class", background=self.default_color, font=("Arial", 11, "bold"))
        class_label.grid(row=0, column=0, sticky="w", pady=(0, 5))

        actor_class_combobox = ttk.Combobox(class_frame, textvariable=self.choose_class, state="readonly")
        actor_class_combobox["values"] = self.available_classes
        actor_class_combobox.current(0)
        actor_class_combobox.bind("<<ComboboxSelected>>", self._update_model)
        actor_class_combobox.grid(row=1, column=0, sticky="ew", padx=(8, 0))

        # Model
        model_label = ttk.Label(model_frame, text="Model", background=self.default_color, font=("Arial", 11, "bold"))
        model_label.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.actor_model = filter_carla_id_by_type(self.actor_type, self.choose_class.get().lower(),
                                                   self.blueprint_lib)

        self.actor_model_combobox = ttk.Combobox(model_frame, textvariable=self.choose_model, state="readonly")
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)
        self.actor_model_combobox.grid(row=1, column=0, sticky="ew", padx=(8, 0))

        button_frame = ttk.Frame(self, style="new.TFrame")
        button_frame.grid(row=1, column=0, columnspan=2, pady=(16, 0))

        change_button = ttk.Button(button_frame, text="Change Model", command=self.change_model)
        change_button.pack(side="left", padx=(0, 5))
        cancel_button = ttk.Button(button_frame, text="Cancel", command=self.destroy)
        cancel_button.pack(side="left", padx=(5, 0))

    def _update_model(self, event):
        self.actor_model = filter_carla_id_by_type(self.actor_type, self.choose_class.get().lower(),
                                                         self.blueprint_lib)
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)

    def change_model(self):
        selected_model = self.choose_model.get()
        selected_class = self.choose_class.get().lower()
        if selected_model:
            self.on_model_change_callback(selected_class, selected_model)
            self.destroy()
