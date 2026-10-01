import tkinter as tk
from tkinter import colorchooser
from tkinter import ttk

class ColorPicker(ttk.Frame):
    def __init__(self, parent, color="#FFFFFF"):
        super().__init__(parent)
        self.color = color

        self.preview = tk.Label(self, width=4, height=2, bg=self.color, relief="solid", bd=1)

        self.preview.pack(side="left", padx=5)

        self.color_label = ttk.Label(self, text=self.color)
        self.color_label.pack(side="left", padx=5)

        self.button = ttk.Button(self, text="Choose Color", command=self.choose_color)
        self.button.pack(side="left", padx=5)

    def choose_color(self):
        rgb, hex_color = colorchooser.askcolor(title="Choose a color", initialcolor=self.color, color=self.color)
        if hex_color is None:
            return

        self.color = hex_color
        self.preview.config(bg=self.color)
        self.color_label.config(text=self.color)
