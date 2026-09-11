"""Tkinter thin adapter for CreatorService contract v1.0."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from PIL import Image, ImageOps, ImageTk

from creator_service import CreatorRequest, CreatorService, SERVICE_CONTRACT_VERSION
from inspection_service import InspectionResult, InspectionService
from gui.branding import (
    APP_ICON_PATH,
    APP_ICON_ICO_PATH,
    ACTION_LABEL,
    CANCEL_LABEL,
    DISCLAIMER,
    IMAGE_PICKER_LABEL,
    INSPECT_LABEL,
    LOCAL_PROCESSING_NOTE,
    PRODUCT_NAME,
    PRODUCT_NAME_EN,
    RIGHTS_DESCRIPTION,
    RIGHTS_LABEL,
    RIGHTS_SECTION,
    RIGHTS_USAGE_NOTICE,
    SUPPORTED_FORMATS_LABEL,
    TAGLINE,
)
from gui.inspection_presenter import (
    PreflightWarning,
    inspection_explanation,
    inspection_heading,
    inspection_primary_rows,
    inspection_technical_rows,
    preflight_warning,
    signature_explanation,
)
from gui.messages import ResultMessage, message_for_result
from gui.theme import BACKGROUND, configure_styles
from trustmark_model_manager import ModelPreparationError, is_model_preparation_in_progress


@dataclass(frozen=True)
class AdapterOutcome:
    output_path: Path
    service_result: dict[str, Any]
    message: ResultMessage


@dataclass(frozen=True)
class InspectionOutcome:
    inspection_result: InspectionResult


PREVIEW_MAX_SIZE = (220, 150)
INITIAL_WINDOW_SIZE = (620, 720)
SECTION_TO_CONTROL_GAP = 8
CONTROL_TO_PANEL_GAP = 14


def preview_size_for(
    width: int,
    height: int,
    maximum: tuple[int, int] = PREVIEW_MAX_SIZE,
) -> tuple[int, int]:
    """Return a no-upscale preview size that preserves the source aspect ratio."""
    if width <= 0 or height <= 0:
        raise ValueError("preview source dimensions must be positive")
    scale = min(maximum[0] / width, maximum[1] / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def output_path_for(input_path: Path) -> Path:
    """Apply the fixed MVP naming rule without touching the filesystem."""
    return input_path.with_name(f"{input_path.stem}_rights{input_path.suffix}")


class CreatorGuiAdapter:
    """Translate GUI intent to and from CreatorService contract v1.0."""

    def __init__(
        self,
        service: CreatorService | None = None,
        inspector: InspectionService | None = None,
    ) -> None:
        self.service = service or CreatorService()
        self.inspector = inspector or InspectionService()

    def create(self, input_path: Path, cancel_check: Callable[[], bool] | None = None) -> AdapterOutcome:
        output_path = output_path_for(input_path)
        request = CreatorRequest.from_mapping(
            {"inputPath": str(input_path), "outputPath": str(output_path)}
        )
        result = self.service.create(request, cancel_check=cancel_check)
        if result.get("serviceContractVersion") != SERVICE_CONTRACT_VERSION:
            message = message_for_result({"status": "INTERNAL_ERROR", "errorCode": "INTERNAL_ERROR"})
        else:
            message = message_for_result(result)
        return AdapterOutcome(output_path, result, message)

    def inspect(self, input_path: Path) -> InspectionOutcome:
        return InspectionOutcome(self.inspector.inspect(input_path))


class CreatorWindow:
    POLL_MS = 100

    def __init__(self, root: tk.Tk, adapter: CreatorGuiAdapter | None = None) -> None:
        self.root = root
        self.adapter = adapter or CreatorGuiAdapter()
        self.input_path: Path | None = None
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.completions: queue.Queue[AdapterOutcome | InspectionOutcome | BaseException] = queue.Queue()
        self.operation: str | None = None

        self.file_name = tk.StringVar(value="画像が選択されていません")
        self.choose_label = tk.StringVar(value=IMAGE_PICKER_LABEL)
        self.preview_message = tk.StringVar(value="")
        self.status_heading = tk.StringVar(value="画像を選択してください")
        self.status_detail = tk.StringVar(value="")
        self.developer_code = tk.StringVar(value="")
        self.output_path_display = tk.StringVar(value="")
        self.last_output_path: Path | None = None
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._configure_window()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self) -> None:
        self.root.title(f"{PRODUCT_NAME} — {PRODUCT_NAME_EN}")
        self.root.geometry(f"{INITIAL_WINDOW_SIZE[0]}x{INITIAL_WINDOW_SIZE[1]}")
        self.root.minsize(560, 650)
        self.root.configure(background=BACKGROUND)
        self.font_family = configure_styles(self.root)
        self._set_app_icon()

    def _set_app_icon(self) -> None:
        required_paths = [APP_ICON_PATH]
        if os.name == "nt":
            required_paths.append(APP_ICON_ICO_PATH)
        missing = [path for path in required_paths if not path.is_file()]
        if missing:
            raise RuntimeError(f"required application icon is missing: {missing[0]}")
        try:
            self._icon_image = tk.PhotoImage(file=str(APP_ICON_PATH))
            self.root.iconphoto(True, self._icon_image)
            if os.name == "nt":
                self.root.iconbitmap(default=str(APP_ICON_ICO_PATH))
            # Tk creates the native Windows wrapper when the root is mapped.
            # Reapply to that concrete window after mapping so the title bar and
            # the taskbar use the same Shirushi icon.
            self.root.after_idle(self._set_mapped_window_icon)
        except tk.TclError as exc:
            raise RuntimeError("failed to load the Shirushi application icon") from exc

    def _set_mapped_window_icon(self) -> None:
        try:
            self.root.iconphoto(False, self._icon_image)
            if os.name == "nt":
                self.root.iconbitmap(str(APP_ICON_ICO_PATH))
        except tk.TclError as exc:
            raise RuntimeError("failed to apply the Shirushi window icon") from exc

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=(44, 24, 44, 18))
        outer.pack(fill="both", expand=True)

        footer = ttk.Frame(outer, style="Footer.TFrame")
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        self.footer = footer
        ttk.Label(
            footer,
            text=LOCAL_PROCESSING_NOTE,
            style="Muted.TLabel",
            wraplength=525,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            footer,
            text=DISCLAIMER,
            style="Muted.TLabel",
            wraplength=510,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        content = ttk.Frame(outer, style="App.TFrame")
        content.pack(side="top", fill="both", expand=True)
        self.content = content
        ttk.Label(content, text=PRODUCT_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(content, text=TAGLINE, style="Tagline.TLabel").pack(anchor="w", pady=(4, 14))

        ttk.Label(content, text="画像", style="Section.TLabel").pack(anchor="w")
        self.choose_button = ttk.Button(
            content,
            textvariable=self.choose_label,
            command=self._choose_file,
            style="Quiet.TButton",
        )
        self.choose_button.pack(anchor="w", pady=(SECTION_TO_CONTROL_GAP, 6))
        self.preview_frame = ttk.Frame(content, style="Preview.TFrame")
        self.preview_label = tk.Label(
            self.preview_frame,
            background=BACKGROUND,
            borderwidth=1,
            relief="solid",
        )
        self.preview_label.pack(anchor="w")
        self.preview_message_label = ttk.Label(
            self.preview_frame,
            textvariable=self.preview_message,
            style="Muted.TLabel",
        )
        self.file_name_label = ttk.Label(
            content,
            textvariable=self.file_name,
            style="Body.TLabel",
            wraplength=510,
        )
        self.file_name_label.pack(anchor="w", pady=(2, 0))
        self.supported_formats_label = ttk.Label(
            content,
            text=SUPPORTED_FORMATS_LABEL,
            style="Muted.TLabel",
        )
        self.supported_formats_label.pack(anchor="w", pady=(2, 14))

        ttk.Label(content, text=RIGHTS_SECTION, style="Section.TLabel").pack(anchor="w")
        ttk.Label(content, text=RIGHTS_LABEL, style="Rights.TLabel", wraplength=510).pack(anchor="w", pady=(6, 4))
        ttk.Label(content, text=RIGHTS_DESCRIPTION, style="Muted.TLabel", wraplength=510).pack(anchor="w", pady=(0, 4))
        ttk.Label(content, text=RIGHTS_USAGE_NOTICE, style="Muted.TLabel", wraplength=510).pack(
            anchor="w", pady=(0, 14)
        )

        self.action_button = ttk.Button(
            content,
            text=ACTION_LABEL,
            command=self._start_create,
            style="Action.TButton",
            width=28,
            state="disabled",
        )
        self.action_button.pack(anchor="w")
        ttk.Label(
            content,
            text="すでに付いているしるしを確認",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(14, 0))
        self.inspect_button = ttk.Button(
            content,
            text=INSPECT_LABEL,
            command=self._start_inspection,
            style="Secondary.TButton",
            width=18,
            state="disabled",
        )
        self.inspect_button.pack(anchor="w", pady=(SECTION_TO_CONTROL_GAP, 0))

        self.inspection_panel = ttk.Frame(content, style="Placeholder.TFrame", padding=(10, 7))
        self.inspection_panel.pack(fill="x", pady=(CONTROL_TO_PANEL_GAP, 0))
        self._show_inspection_placeholder()
        self.status_block = ttk.Frame(content, style="App.TFrame")
        self.status_block.pack(fill="x", pady=(14, 0))
        self.status_heading_label = ttk.Label(self.status_block, textvariable=self.status_heading, style="Status.TLabel", wraplength=510)
        self.status_heading_label.pack(anchor="w")
        self.status_detail_label = ttk.Label(self.status_block, textvariable=self.status_detail, style="Body.TLabel", wraplength=510, justify="left")
        self.status_detail_label.pack(anchor="w", pady=(6, 2))
        self.developer_code_label = ttk.Label(self.status_block, textvariable=self.developer_code, style="Muted.TLabel")
        self.developer_code_label.pack(anchor="w", pady=(2, 0))

        self.output_block = ttk.Frame(content, style="App.TFrame")
        ttk.Label(
            self.output_block,
            textvariable=self.output_path_display,
            style="SavedFile.TLabel",
            wraplength=510,
            justify="left",
        ).pack(anchor="w")
        ttk.Button(
            self.output_block,
            text="保存先を開く",
            command=self._open_output_folder,
            style="Secondary.TButton",
        ).pack(anchor="w", pady=(5, 0))

    def _choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="画像を選択",
            filetypes=(
                ("Supported images", "*.png *.jpg *.jpeg *.jfif"),
                ("PNG images", "*.png"),
                ("JPEG images", "*.jpg *.jpeg *.jfif"),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            return
        self.select_input(Path(selected))

    def select_input(self, path: Path) -> None:
        self.input_path = path
        self.file_name.set(path.name)
        self.choose_label.set("画像を変更する")
        self._update_preview(path)
        self._clear_inspection_panel()
        self._set_status_presentation(False)
        self.status_heading.set("準備できました")
        self.status_detail.set(f"保存予定: {output_path_for(path).name}")
        self.developer_code.set("")
        self.action_button.configure(state="normal")
        self.inspect_button.configure(state="normal")
        self.output_block.pack_forget()
        self.output_path_display.set("")
        self.last_output_path = None
        self._fit_window_to_content()

    def _update_preview(self, path: Path) -> None:
        try:
            with Image.open(path) as source:
                oriented = ImageOps.exif_transpose(source)
                preview = oriented.convert("RGB")
                preview = preview.resize(preview_size_for(*preview.size), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(preview)
            self.preview_label.configure(image=self._preview_photo, text="")
            self.preview_label.image = self._preview_photo
            self.preview_message.set("")
            self.preview_message_label.pack_forget()
        except (OSError, ValueError):
            self._preview_photo = None
            self.preview_label.configure(image="", text="プレビューを表示できません")
            self.preview_label.image = None
            self.preview_message.set("画像ファイルは変更されていません。")
            self.preview_message_label.pack(anchor="w", pady=(3, 0))
        if not self.preview_frame.winfo_manager():
            self.preview_frame.pack(anchor="w", pady=(0, 4), before=self.file_name_label)

    def _clear_inspection_panel(self) -> None:
        self._show_inspection_placeholder()
        if self.input_path is not None and self._preview_photo is not None and not self.preview_frame.winfo_manager():
            self.preview_frame.pack(anchor="w", pady=(0, 4), before=self.file_name_label)
        if not self.status_block.winfo_manager():
            self.status_block.pack(fill="x", pady=(14, 0))

    def _fit_window_to_content(self) -> None:
        self.root.update_idletasks()
        desired = max(
            INITIAL_WINDOW_SIZE[1],
            self.content.winfo_reqheight() + self.footer.winfo_reqheight() + 90,
        )
        maximum = max(720, self.root.winfo_screenheight() - 80)
        self.root.geometry(f"{INITIAL_WINDOW_SIZE[0]}x{min(desired, maximum)}")

    def _set_processing(self, processing: bool) -> None:
        if processing:
            self.choose_button.configure(state="disabled")
            self.inspect_button.configure(state="disabled")
            self.action_button.configure(text=CANCEL_LABEL, command=self._request_cancel, state="normal")
        else:
            self.choose_button.configure(state="normal")
            self.inspect_button.configure(state="normal" if self.input_path is not None else "disabled")
            self.action_button.configure(
                text=ACTION_LABEL,
                command=self._start_create,
                state="normal" if self.input_path is not None else "disabled",
            )

    def _start_create(self) -> None:
        if self.input_path is None or (self.worker is not None and self.worker.is_alive()):
            return
        selected_path = self.input_path
        self._set_status_presentation(False)
        self.operation = "preflight_create"
        self.cancel_event.clear()
        self._set_processing(True)
        self.status_heading.set("付与前に画像を確認しています…")
        self.status_detail.set("既存のしるし情報がないか確認しています。")
        self.developer_code.set("")
        self.output_block.pack_forget()
        self.output_path_display.set("")
        self.last_output_path = None
        self._show_preflight_progress()
        self.worker = threading.Thread(target=self._run_inspection, args=(selected_path,), daemon=True)
        self.worker.start()
        self.root.after(self.POLL_MS, self._poll_completion)

    def _begin_create(self, selected_path: Path, *, preserve_inspection: bool = False) -> None:
        if not preserve_inspection:
            self._clear_inspection_panel()
        self.operation = "create"
        self.cancel_event.clear()
        self._set_processing(True)
        self.status_heading.set("権利情報を付与しています…")
        self.status_detail.set("保存内容の確認まで、このままお待ちください。")
        self.developer_code.set("")
        self.worker = threading.Thread(target=self._run_service, args=(selected_path,), daemon=True)
        self.worker.start()
        self.root.after(self.POLL_MS, self._poll_completion)

    def _run_service(self, selected_path: Path) -> None:
        try:
            self.completions.put(self.adapter.create(selected_path, self.cancel_event.is_set))
        except BaseException as exc:
            self.completions.put(exc)

    def _start_inspection(self) -> None:
        if self.input_path is None or (self.worker is not None and self.worker.is_alive()):
            return
        selected_path = self.input_path
        self.operation = "inspect"
        self.choose_button.configure(state="disabled")
        self.action_button.configure(state="disabled")
        self.inspect_button.configure(state="disabled")
        self._show_inspection_progress()
        self.worker = threading.Thread(target=self._run_inspection, args=(selected_path,), daemon=True)
        self.worker.start()
        self.root.after(self.POLL_MS, self._poll_completion)

    def _run_inspection(self, selected_path: Path) -> None:
        try:
            self.completions.put(self.adapter.inspect(selected_path))
        except BaseException as exc:
            self.completions.put(exc)

    def _poll_completion(self) -> None:
        try:
            completed = self.completions.get_nowait()
        except queue.Empty:
            if self.worker is not None and self.worker.is_alive():
                if is_model_preparation_in_progress():
                    self._set_status_presentation(False)
                    self.status_heading.set("初回準備中")
                    self.status_detail.set("初回のみ時間がかかる場合があります。しばらくお待ちください。")
                self.root.after(self.POLL_MS, self._poll_completion)
            return
        operation = self.operation
        self.operation = None
        self.worker = None
        if operation in {"create", "preflight_create"}:
            self._set_processing(False)
        else:
            self.choose_button.configure(state="normal")
            self.action_button.configure(state="normal" if self.input_path is not None else "disabled")
            self.inspect_button.configure(state="normal" if self.input_path is not None else "disabled")
        if isinstance(completed, BaseException):
            traceback.print_exception(completed)
            if isinstance(completed, ModelPreparationError):
                self._show_message(
                    message_for_result({"status": "WRITE_FAILED", "errorCode": completed.code})
                )
                if operation in {"inspect", "preflight_create"}:
                    self._show_inspection_error()
                return
            if operation == "inspect":
                self._show_inspection_error()
                return
            if operation == "preflight_create":
                self._show_preflight_error()
                return
            message = message_for_result({"status": "INTERNAL_ERROR", "errorCode": "INTERNAL_ERROR"})
            self._show_message(message)
            return
        if isinstance(completed, InspectionOutcome):
            if operation == "preflight_create":
                self._handle_preflight_result(completed.inspection_result)
                return
            try:
                self._show_inspection(completed.inspection_result)
            except (tk.TclError, TypeError, ValueError):
                traceback.print_exc()
                self._show_inspection_error()
            return
        self._show_message(completed.message)
        if completed.message.successful:
            self.last_output_path = completed.output_path.resolve()
            self.output_path_display.set(completed.output_path.name)
            self.output_block.pack(fill="x", pady=(7, 0), after=self.status_block)
            self._fit_window_to_content()

    def _handle_preflight_result(self, result: InspectionResult) -> None:
        selected_path = self.input_path
        if selected_path is None:
            return
        if self.cancel_event.is_set():
            self._show_message(message_for_result({"status": "CANCELLED", "errorCode": "CANCELLED_BY_REQUEST"}))
            return
        warning = preflight_warning(result)
        if warning is None:
            self._begin_create(selected_path)
            return
        self._show_inspection(result)
        self._show_preflight_warning(warning)
        if self._confirm_reapply(warning):
            self._begin_create(selected_path, preserve_inspection=True)

    def _show_preflight_warning(self, warning: PreflightWarning) -> None:
        self._set_status_presentation(False)
        self.status_heading.set(warning.heading)
        self.status_detail.set(warning.detail)
        self.developer_code.set("")
        self._fit_window_to_content()

    def _confirm_reapply(self, warning: PreflightWarning) -> bool:
        return messagebox.askyesno(
            title="既存のしるし情報",
            message=warning.heading,
            detail=f"{warning.detail}\n\nそれでもしるしを付けますか？",
            icon="warning",
            default="no",
            parent=self.root,
        )

    def _show_inspection(self, result: InspectionResult) -> None:
        for child in self.inspection_panel.winfo_children():
            child.destroy()
        frame = self.inspection_panel
        frame.configure(style="ResultPanel.TFrame", padding=(14, 10))
        ttk.Label(
            frame,
            text=inspection_heading(result),
            style="ResultTitle.TLabel",
            wraplength=480,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=inspection_explanation(result),
            style="ResultLead.TLabel",
            wraplength=480,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
        result_body = ttk.Frame(frame, style="ResultBody.TFrame")
        result_body.pack(fill="x", pady=(8, 0))
        detail_content = ttk.Frame(result_body, style="ResultBody.TFrame")
        detail_content.pack(side="left", fill="x", expand=True)
        rows = ttk.Frame(detail_content, style="ResultBody.TFrame")
        rows.pack(fill="x")
        for label, value in inspection_primary_rows(result):
            row = ttk.Frame(rows, style="ResultBody.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, style="Body.TLabel", width=20).pack(side="left", anchor="w")
            ttk.Label(row, text=value, style="PrimaryValue.TLabel").pack(side="left", anchor="w")
        detail_frame = ttk.Frame(detail_content, style="ResultBody.TFrame")
        ttk.Label(detail_frame, text="技術情報", style="TechnicalHeading.TLabel").pack(anchor="w")
        technical_rows = ttk.Frame(detail_frame, style="App.TFrame")
        technical_rows.pack(fill="x", pady=(8, 6))
        for label, value in inspection_technical_rows(result):
            row = ttk.Frame(technical_rows, style="App.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, style="Muted.TLabel", width=20).pack(side="left", anchor="w")
            ttk.Label(row, text=value, style="Body.TLabel").pack(side="left", anchor="w")
        signature_note = signature_explanation(result)
        if signature_note:
            ttk.Label(
                detail_frame,
                text=signature_note,
                style="ResultMuted.TLabel",
                wraplength=480,
                justify="left",
            ).pack(anchor="w", pady=(4, 4))
        if result.warnings:
            ttk.Label(
                detail_frame,
                text="\n".join(result.warnings),
                style="ResultMuted.TLabel",
                wraplength=480,
                justify="left",
                ).pack(anchor="w", pady=(4, 8))

        def toggle_details() -> None:
            if detail_frame.winfo_manager():
                detail_frame.pack_forget()
                rows.pack(fill="x")
                detail_button.configure(text="詳細を見る")
            else:
                rows.pack_forget()
                detail_frame.pack(fill="x")
                detail_button.configure(text="詳細を閉じる")
            self._fit_window_to_content()

        detail_button = ttk.Button(
            result_body,
            text="詳細を見る",
            command=toggle_details,
            style="Detail.TButton",
        )
        detail_button.pack(side="right", anchor="center", padx=(12, 0))
        self._fit_window_to_content()

    def _show_inspection_placeholder(self) -> None:
        for child in self.inspection_panel.winfo_children():
            child.destroy()
        self.inspection_panel.configure(style="Placeholder.TFrame", padding=(10, 7))
        ttk.Label(
            self.inspection_panel,
            text="確認結果がここに表示されます",
            style="PlaceholderTitle.TLabel",
        ).pack(anchor="w")

    def _show_inspection_progress(self) -> None:
        for child in self.inspection_panel.winfo_children():
            child.destroy()
        self.inspection_panel.configure(style="Placeholder.TFrame", padding=(10, 7))
        ttk.Label(
            self.inspection_panel,
            text="しるしを確認しています…",
            style="PlaceholderTitle.TLabel",
        ).pack(anchor="w")

    def _show_preflight_progress(self) -> None:
        for child in self.inspection_panel.winfo_children():
            child.destroy()
        self.inspection_panel.configure(style="Placeholder.TFrame", padding=(10, 7))
        ttk.Label(
            self.inspection_panel,
            text="既存のしるし情報を確認しています…",
            style="PlaceholderTitle.TLabel",
        ).pack(anchor="w")

    def _show_preflight_error(self) -> None:
        self._set_status_presentation(False)
        self.status_heading.set("付与前の確認を完了できませんでした")
        self.status_detail.set("しるしは付けていません。画像の状態を確認して、もう一度お試しください。")
        self.developer_code.set("")
        self._show_inspection_error()

    def _show_inspection_error(self) -> None:
        for child in self.inspection_panel.winfo_children():
            child.destroy()
        self.inspection_panel.configure(style="ResultPanel.TFrame", padding=(14, 12))
        ttk.Label(
            self.inspection_panel,
            text="しるしを確認できませんでした",
            style="ResultTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            self.inspection_panel,
            text="画像は変更されていません。もう一度お試しください。",
            style="ResultMuted.TLabel",
        ).pack(anchor="w", pady=(6, 0))
        self._fit_window_to_content()

    def _open_output_folder(self) -> None:
        if self.last_output_path is not None and self.last_output_path.parent.is_dir():
            os.startfile(self.last_output_path.parent)

    def _show_message(self, message: ResultMessage) -> None:
        self._set_status_presentation(message.successful)
        self.status_heading.set(message.heading)
        self.status_detail.set(message.detail)
        self.developer_code.set(f"Code: {message.developer_code}" if message.developer_code else "")

    def _set_status_presentation(self, successful: bool) -> None:
        if successful:
            self.status_block.configure(style="App.TFrame", padding=(0, 0))
            self.status_heading_label.configure(style="SuccessTitle.TLabel")
            self.status_detail_label.configure(style="SuccessBody.TLabel")
        else:
            self.status_block.configure(style="App.TFrame", padding=(0, 0))
            self.status_heading_label.configure(style="Status.TLabel")
            self.status_detail_label.configure(style="Body.TLabel")

    def _request_cancel(self) -> None:
        self._set_status_presentation(False)
        self.cancel_event.set()
        self.action_button.configure(text="キャンセル要求中…", state="disabled")
        self.status_heading.set("キャンセルを要求しています…")
        self.status_detail.set("現在の安全な処理区間が完了するまでお待ちください。")

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if self.operation in {"create", "preflight_create"}:
                self._request_cancel()
            else:
                self.status_heading.set("しるしを確認しています…")
                self.status_detail.set("確認が完了してから画面を閉じてください。")
            return
        self.root.destroy()


def run() -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Shirushi.Creator.v0.1")
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    CreatorWindow(root)
    root.mainloop()
