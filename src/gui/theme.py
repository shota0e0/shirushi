"""Centralized, platform-aware Tk typography and visual styles."""

from __future__ import annotations

from dataclasses import dataclass
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk


WINDOWS_FONT_CANDIDATES = ("Yu Gothic UI", "Segoe UI")
MACOS_FONT_CANDIDATES = ("Hiragino Sans", "Helvetica Neue")
FALLBACK_FONT = "TkDefaultFont"

BACKGROUND = "#FAFAF8"
TEXT = "#24272C"
MUTED = "#666B73"
ACCENT = "#324F43"
PLACEHOLDER_BACKGROUND = "#F8F8F6"


@dataclass(frozen=True)
class FontFamily:
    name: str
    source: str


def resolve_font_family(root: tk.Misc) -> FontFamily:
    available = set(tkfont.families(root))
    candidates = MACOS_FONT_CANDIDATES if sys.platform == "darwin" else WINDOWS_FONT_CANDIDATES
    for candidate in candidates:
        if candidate in available:
            return FontFamily(candidate, "platform")
    return FontFamily(FALLBACK_FONT, "tk-default")


def configure_styles(root: tk.Misc) -> FontFamily:
    family = resolve_font_family(root)
    style = ttk.Style(root)
    if sys.platform.startswith("win") and "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure("App.TFrame", background=BACKGROUND)
    style.configure("Footer.TFrame", background=BACKGROUND)
    style.configure("Preview.TFrame", background=BACKGROUND)
    style.configure("ResultPanel.TFrame", background=BACKGROUND, relief="solid", borderwidth=1)
    style.configure("ResultBody.TFrame", background=BACKGROUND, relief="flat", borderwidth=0)
    style.configure("Placeholder.TFrame", background=PLACEHOLDER_BACKGROUND, relief="flat", borderwidth=0)
    style.configure("Title.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 20, "bold"))
    style.configure("Tagline.TLabel", background=BACKGROUND, foreground=MUTED, font=(family.name, 11))
    style.configure("Section.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 10, "bold"))
    style.configure("Rights.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 11))
    style.configure("Body.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 10))
    style.configure("Muted.TLabel", background=BACKGROUND, foreground=MUTED, font=(family.name, 9))
    style.configure("Status.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 11))
    style.configure("ResultLead.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 10))
    style.configure("ResultTitle.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 14, "bold"))
    style.configure("ResultMuted.TLabel", background=BACKGROUND, foreground=MUTED, font=(family.name, 9))
    style.configure("PrimaryValue.TLabel", background=BACKGROUND, foreground=ACCENT, font=(family.name, 11, "bold"))
    style.configure("SavedFile.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 10, "bold"))
    style.configure("TechnicalHeading.TLabel", background=BACKGROUND, foreground=TEXT, font=(family.name, 10, "bold"))
    style.configure("SuccessTitle.TLabel", background=BACKGROUND, foreground=ACCENT, font=(family.name, 14, "bold"))
    style.configure("SuccessBody.TLabel", background=BACKGROUND, foreground=MUTED, font=(family.name, 10))
    style.configure("PlaceholderTitle.TLabel", background=PLACEHOLDER_BACKGROUND, foreground=MUTED, font=(family.name, 8))
    style.configure("Action.TButton", font=(family.name, 11, "bold"), padding=(22, 10))
    style.configure("Quiet.TButton", font=(family.name, 10), padding=(12, 7))
    style.configure("Secondary.TButton", font=(family.name, 9), padding=(9, 5))
    style.configure("Detail.TButton", font=(family.name, 9), padding=(7, 4))
    return family
