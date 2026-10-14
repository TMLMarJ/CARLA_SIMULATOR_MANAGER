settings={
    # =========================================================
    # FRAMES
    # =========================================================
    "TFrame": {
        "configure": {
            "background": "#F7E7D3"
        }
    },

    "new.TFrame": {
        "configure": {
            "background": "#F7E7D3"
        }
    },

    "Section.TLabelframe": {
        "configure": {
            "background": "#F7E7D3",
            "bordercolor": "#E7B36C",
            "borderwidth": 1,
            "relief": "solid"
        }
    },

    "Section.TLabelframe.Label": {
        "configure": {
            "background": "#F7E7D3",
            "foreground": "#4A2E1D",
            "font": ("Segoe UI", 10, "bold")
        }
    },

    # =========================================================
    # NOTEBOOK
    # =========================================================
    "TNotebook": {
        "configure": {
            "background": "#F7E7D3",
            "borderwidth": 0
        }
    },

    "TNotebook.Tab": {
        "configure": {
            "background": "#E7B36C",
            "foreground": "#4A2E1D",
            "padding": [14, 8],
            "borderwidth": 0
        },
        "map": {
            "background": [
                ("selected", "#F1D0A3"),
                ("active", "#E7B36C")
            ],
            "foreground": [
                ("selected", "#4A2E1D"),
                ("active", "#4A2E1D")
            ]
        }
    },

    # =========================================================
    # LABEL
    # =========================================================
    "TLabel": {
        "configure": {
            "background": "#F7E7D3",
            "foreground": "#4A2E1D"
        }
    },

    # =========================================================
    # BUTTON
    # =========================================================
    "TButton": {
        "configure": {
            "background": "#C96D3C",
            "foreground": "#FFF8F5",
            "padding": [14, 8],
            "borderwidth": 0,
            "relief": "flat",
            "font": ("Segoe UI", 9, "bold")
        },
        "map": {
            "background": [
                ("pressed", "#9E4D27"),
                ("active", "#B55E2F"),
                ("disabled", "#D8B9A5")
            ],
            "foreground": [
                ("disabled", "#F0DDD2"),
                ("pressed", "#FFFFFF"),
                ("active", "#FFF8F5")
            ]
        }
    },

    # =========================================================
    # CHECKBUTTON
    # =========================================================
    "TCheckbutton": {
        "configure": {
            "background": "#F7E7D3",
            "foreground": "#4A2E1D",
            "padding": [4, 4],
            "borderwidth": 0,
            "font": ("Segoe UI", 9)
        },
        "map": {
            "background": [
                ("active", "#F7E7D3")
            ],
            "foreground": [
                ("active", "#C96D3C"),
                ("disabled", "#A88F82")
            ]
        }
    },

    # =========================================================
    # COMBOBOX
    # =========================================================
    "TCombobox": {
        "configure": {
            "fieldbackground": "#FFF9F3",
            "background": "#C96D3C",
            "foreground": "#4A2E1D",
            "padding": 6,
            "borderwidth": 0
        },
        "map": {
            "fieldbackground": [
                ("readonly", "#FFF9F3")
            ],
            "selectbackground": [
                ("focus", "#E7B36C")
            ],
            "selectforeground": [
                ("focus", "#4A2E1D")
            ]
        }
    },

    # =========================================================
    # SPINBOX
    # =========================================================
    "TSpinbox": {
        "configure": {
            "fieldbackground": "#FFF9F3",
            "background": "#F9D7A8",
            "foreground": "#4A2E1D",
            "padding": 4,
            "borderwidth": 0
        }
    },

    # =========================================================
    # TREEVIEW
    # =========================================================
    "Treeview": {
        "configure": {
            "background": "#FFF9F3",
            "fieldbackground": "#FFF9F3",
            "foreground": "#4A2E1D",
            "rowheight": 28,
            "borderwidth": 0,
            "padding": 0
        },
        "map": {
            "background": [
                ("selected", "#E7B36C")
            ],
            "foreground": [
                ("selected", "#4A2E1D")
            ]
        }
    },

    "Treeview.Heading": {
        "configure": {
            "background": "#F1D0A3",
            "foreground": "#4A2E1D",
            "padding": [8, 8],
            "borderwidth": 0,
            "font": ("Segoe UI", 9, "bold")
        },
        "map": {
            "background": [
                ("active", "#E7B36C")
            ],
            "foreground": [
                ("active", "#4A2E1D")
            ]
        }
    }
}