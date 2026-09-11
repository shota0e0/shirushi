from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gui.app import (
    INITIAL_WINDOW_SIZE,
    PREVIEW_MAX_SIZE,
    CreatorGuiAdapter,
    InspectionOutcome,
    output_path_for,
    preview_size_for,
)
from gui.branding import (
    ACTION_LABEL,
    APP_ICON_ICO_PATH,
    APP_ICON_PATH,
    CANCEL_LABEL,
    DISCLAIMER,
    IMAGE_PICKER_LABEL,
    INSPECT_LABEL,
    LOCAL_PROCESSING_NOTE,
    PRODUCT_DESCRIPTION,
    PRODUCT_NAME,
    PRODUCT_NAME_EN,
    PRODUCT_NAME_IS_PROVISIONAL,
    RIGHTS_DESCRIPTION,
    RIGHTS_LABEL,
    RIGHTS_USAGE_NOTICE,
    SUPPORTED_FORMATS_LABEL,
    TAGLINE,
)
from gui.messages import message_for_result
from gui.theme import FALLBACK_FONT, MACOS_FONT_CANDIDATES, WINDOWS_FONT_CANDIDATES


def service_result(status: str, error_code: str | None = None) -> dict:
    return {
        "serviceContractVersion": "1.0",
        "status": status,
        "errorCode": error_code,
        "message": "service message",
        "input": {"path": "input.png", "sha256": None},
        "output": None,
        "verification": None,
    }


class FakeService:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = []

    def create(self, request, cancel_check=None):
        self.calls.append((request, cancel_check))
        return self.result


class FakeInspector:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    def inspect(self, input_path: Path):
        self.calls.append(input_path)
        return self.result


class CreatorGuiAdapterTests(unittest.TestCase):
    APP_SOURCE = (PROJECT / "src/gui/app.py").read_text(encoding="utf-8")

    def test_valid_input_maps_exact_service_request(self) -> None:
        fake = FakeService(service_result("SUCCESS"))
        input_path = Path(r"C:\images\sample.png")
        cancel_check = lambda: False
        outcome = CreatorGuiAdapter(fake).create(input_path, cancel_check)
        request, actual_cancel = fake.calls[0]
        self.assertEqual(input_path, request.input_path)
        self.assertEqual(Path(r"C:\images\sample_rights.png"), request.output_path)
        self.assertIs(cancel_check, actual_cancel)
        self.assertEqual(Path(r"C:\images\sample_rights.png"), outcome.output_path)

    def test_output_path_generation(self) -> None:
        self.assertEqual(Path("photo_rights.png"), output_path_for(Path("photo.png")))
        self.assertEqual(Path(r"C:\a.b\photo.final_rights.PNG"), output_path_for(Path(r"C:\a.b\photo.final.PNG")))
        self.assertEqual(Path("photo_rights.jpg"), output_path_for(Path("photo.jpg")))
        self.assertEqual(Path("photo_rights.jpeg"), output_path_for(Path("photo.jpeg")))

    def test_inspection_maps_selected_path_without_creation(self) -> None:
        marker = object()
        inspector = FakeInspector(marker)
        outcome = CreatorGuiAdapter(FakeService(service_result("SUCCESS")), inspector).inspect(Path("sample.png"))
        self.assertIsInstance(outcome, InspectionOutcome)
        self.assertIs(marker, outcome.inspection_result)
        self.assertEqual([Path("sample.png")], inspector.calls)

    def test_success_mapping(self) -> None:
        message = message_for_result(service_result("SUCCESS"))
        self.assertTrue(message.successful)
        self.assertEqual("✔ しるしを付けました", message.heading)
        self.assertEqual("保存と確認が正常に完了しました。", message.detail)

    def test_invalid_request_mapping(self) -> None:
        message = message_for_result(service_result("INVALID_REQUEST", "INVALID_INPUT"))
        self.assertFalse(message.successful)
        self.assertIn("有効なPNGまたはJPEG", message.detail)

    def test_write_failed_mapping(self) -> None:
        message = message_for_result(service_result("WRITE_FAILED", "OUTPUT_PREPARATION_FAILED"))
        self.assertEqual("画像を保存できませんでした", message.heading)
        self.assertEqual("OUTPUT_PREPARATION_FAILED", message.developer_code)

    def test_verification_failed_mapping(self) -> None:
        message = message_for_result(service_result("VERIFICATION_FAILED", "POST_WRITE_VERIFICATION_FAILED"))
        self.assertIn("検証に失敗", message.heading)
        self.assertEqual("検証に失敗した画像は、保存先には出力していません。", message.detail)

    def test_internal_error_mapping_hides_service_message(self) -> None:
        result = service_result("INTERNAL_ERROR", "INTERNAL_ERROR")
        result["message"] = "secret developer traceback"
        message = message_for_result(result)
        self.assertEqual("予期しないエラーが発生しました", message.heading)
        self.assertNotIn("secret", message.heading + message.detail)

    def test_existing_output_non_overwrite_message(self) -> None:
        message = message_for_result(service_result("INVALID_REQUEST", "OUTPUT_ALREADY_EXISTS"))
        self.assertIn("上書きしていません", message.detail)

    def test_cancel_mapping(self) -> None:
        message = message_for_result(service_result("CANCELLED", "CANCELLED_BY_REQUEST"))
        self.assertIn("キャンセル", message.heading)
        self.assertFalse(message.successful)

    def test_contract_version_mismatch_is_safe_internal_message(self) -> None:
        result = service_result("SUCCESS")
        result["serviceContractVersion"] = "2.0"
        outcome = CreatorGuiAdapter(FakeService(result)).create(Path("sample.png"))
        self.assertFalse(outcome.message.successful)
        self.assertEqual("INTERNAL_ERROR", outcome.message.developer_code)

    def test_gui_does_not_import_core_implementation_modules(self) -> None:
        forbidden = {"trustmark", "creator_e2e", "creator_verify", "c2pa"}
        for relative in ("src/gui/app.py", "src/gui/messages.py", "scripts/creator_gui.py"):
            source = (PROJECT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertTrue(forbidden.isdisjoint(imported), f"{relative}: forbidden imports {forbidden & imported}")

    def test_provisional_product_strings_are_centralized(self) -> None:
        self.assertEqual("しるし", PRODUCT_NAME)
        self.assertEqual("Shirushi", PRODUCT_NAME_EN)
        self.assertEqual("創作しるし", PRODUCT_DESCRIPTION)
        self.assertEqual("作品に利用意思を残す", TAGLINE)
        self.assertTrue(PRODUCT_NAME_IS_PROVISIONAL)
        self.assertNotIn("rights-signal-lab", " ".join((PRODUCT_NAME, PRODUCT_NAME_EN, PRODUCT_DESCRIPTION, TAGLINE)))

    def test_rights_copy_omits_fixed_implementation_word(self) -> None:
        self.assertEqual("AIによる学習・生成利用を希望しない", RIGHTS_LABEL)
        self.assertEqual("AI利用に関する意思を画像に記録します", RIGHTS_DESCRIPTION)
        self.assertNotIn("固定", RIGHTS_LABEL + RIGHTS_DESCRIPTION)

    def test_ownership_and_permission_notices_are_narrow_and_visible(self) -> None:
        self.assertEqual("必要な権利または許諾を持つ画像に使用してください。", RIGHTS_USAGE_NOTICE)
        self.assertIn(
            "しるしは、画像の著作権者・権利者本人であることを確認または証明するものではありません。",
            DISCLAIMER,
        )
        runtime_copy = " ".join((RIGHTS_LABEL, RIGHTS_DESCRIPTION, RIGHTS_USAGE_NOTICE, DISCLAIMER))
        for unsupported_claim in (
            "著作権者確認済み",
            "権利者確認済み",
            "creator verified",
            "owner verified",
            "authenticated creator",
        ):
            self.assertNotIn(unsupported_claim, runtime_copy)

        rights_description_position = self.APP_SOURCE.index("text=RIGHTS_DESCRIPTION")
        notice_position = self.APP_SOURCE.index("text=RIGHTS_USAGE_NOTICE")
        action_position = self.APP_SOURCE.index("self.action_button = ttk.Button")
        self.assertLess(rights_description_position, notice_position)
        self.assertLess(notice_position, action_position)

    def test_disclaimer_is_preserved_verbatim(self) -> None:
        self.assertEqual(
            "AI利用に関する意思表示を、対応システムが読み取れる形で画像に追加します。\n"
            "すべてのAIやサービスがこの指定を尊重することを保証するものではありません。\n"
            "しるしは、画像の著作権者・権利者本人であることを確認または証明するものではありません。",
            DISCLAIMER,
        )

    def test_progressbar_dependency_removed(self) -> None:
        self.assertNotIn("Progressbar", self.APP_SOURCE)
        self.assertNotIn("self.progress", self.APP_SOURCE)

    def test_create_and_cancel_share_one_action_slot(self) -> None:
        self.assertEqual(1, self.APP_SOURCE.count("self.action_button = ttk.Button"))
        self.assertNotIn("cancel_button", self.APP_SOURCE)
        self.assertEqual("しるしを付ける", ACTION_LABEL)
        self.assertEqual("キャンセル", CANCEL_LABEL)
        self.assertIn("text=ACTION_LABEL", self.APP_SOURCE)
        self.assertIn("text=CANCEL_LABEL", self.APP_SOURCE)

    def test_success_view_shows_filename_then_folder_action(self) -> None:
        self.assertIn("output_path_display", self.APP_SOURCE)
        self.assertIn("completed.output_path.name", self.APP_SOURCE)
        self.assertNotIn("str(completed.output_path.resolve())", self.APP_SOURCE)
        self.assertNotIn('text="保存先"', self.APP_SOURCE)
        self.assertIn("保存先を開く", self.APP_SOURCE)

    def test_supported_format_copy_and_picker_include_jpeg(self) -> None:
        self.assertEqual("PNG・JPEGに対応しています。", SUPPORTED_FORMATS_LABEL)
        self.assertIn("text=SUPPORTED_FORMATS_LABEL", self.APP_SOURCE)
        self.assertEqual("画像を選ぶ", IMAGE_PICKER_LABEL)
        self.assertIn("value=IMAGE_PICKER_LABEL", self.APP_SOURCE)
        self.assertIn("textvariable=self.choose_label", self.APP_SOURCE)
        self.assertIn("*.jpg", self.APP_SOURCE.lower())
        self.assertIn("*.jpeg", self.APP_SOURCE.lower())
        self.assertEqual("しるしを確認する", INSPECT_LABEL)
        self.assertIn("text=INSPECT_LABEL", self.APP_SOURCE)
        self.assertIn("*.jfif", self.APP_SOURCE.lower())

    def test_inspection_uses_main_window_panel_as_primary_result_surface(self) -> None:
        self.assertIn("self.inspection_panel", self.APP_SOURCE)
        self.assertNotIn("tk.Toplevel", self.APP_SOURCE)
        self.assertIn("inspection_primary_rows(result)", self.APP_SOURCE)
        self.assertIn("inspection_technical_rows(result)", self.APP_SOURCE)
        self.assertLess(
            self.APP_SOURCE.index("inspection_primary_rows(result)"),
            self.APP_SOURCE.index("inspection_technical_rows(result)"),
        )
        self.assertIn('text="技術情報"', self.APP_SOURCE)
        self.assertIn('text="詳細を見る"', self.APP_SOURCE)

    def test_inspection_panel_has_initial_placeholder_and_reuses_same_surface(self) -> None:
        self.assertIn("確認結果がここに表示されます", self.APP_SOURCE)
        self.assertEqual(1, self.APP_SOURCE.count("self.inspection_panel = ttk.Frame"))
        self.assertIn("self._show_inspection_placeholder()", self.APP_SOURCE)

    def test_inspection_updates_only_the_fixed_result_panel(self) -> None:
        show_inspection = self.APP_SOURCE.split("    def _show_inspection(self", 1)[1].split(
            "    def _show_inspection_placeholder", 1
        )[0]
        start_inspection = self.APP_SOURCE.split("    def _start_inspection(self", 1)[1].split(
            "    def _run_inspection", 1
        )[0]
        self.assertNotIn("self.preview_frame.pack_forget()", show_inspection)
        self.assertNotIn("self.status_block.pack_forget()", show_inspection)
        self.assertNotIn("self.inspection_panel.pack", show_inspection)
        self.assertNotIn("self._clear_inspection_panel()", start_inspection)
        self.assertNotIn("self._set_status_presentation", start_inspection)
        self.assertIn("self.preview_label.image = self._preview_photo", self.APP_SOURCE)

    def test_inspection_callback_contains_render_and_worker_failure_boundaries(self) -> None:
        self.assertIn("traceback.print_exception(completed)", self.APP_SOURCE)
        self.assertIn("except (tk.TclError, TypeError, ValueError):", self.APP_SOURCE)
        self.assertIn("self._show_inspection_error()", self.APP_SOURCE)

    def test_secondary_action_uses_shared_spacing_tokens(self) -> None:
        self.assertIn("SECTION_TO_CONTROL_GAP = 8", self.APP_SOURCE)
        self.assertIn("CONTROL_TO_PANEL_GAP = 14", self.APP_SOURCE)
        self.assertIn('self.inspect_button.pack(anchor="w", pady=(SECTION_TO_CONTROL_GAP, 0))', self.APP_SOURCE)
        self.assertIn('self.inspection_panel.pack(fill="x", pady=(CONTROL_TO_PANEL_GAP, 0))', self.APP_SOURCE)

    def test_success_feedback_uses_a_green_heading_without_card_or_technical_copy(self) -> None:
        self.assertIn('style="SuccessTitle.TLabel"', self.APP_SOURCE)
        self.assertNotIn('style="SuccessPanel.TFrame"', self.APP_SOURCE)
        self.assertNotIn("status_technical", self.APP_SOURCE)
        self.assertNotIn("Credential / Durable signalを確認済み", self.APP_SOURCE)
        self.assertNotIn("保存後の検証完了しています。", self.APP_SOURCE)

    def test_only_outer_inspection_panel_uses_bordered_result_style(self) -> None:
        self.assertIn('result_body = ttk.Frame(frame, style="ResultBody.TFrame")', self.APP_SOURCE)
        self.assertIn('detail_content = ttk.Frame(result_body, style="ResultBody.TFrame")', self.APP_SOURCE)
        self.assertIn('rows = ttk.Frame(detail_content, style="ResultBody.TFrame")', self.APP_SOURCE)
        self.assertIn('row = ttk.Frame(rows, style="ResultBody.TFrame")', self.APP_SOURCE)
        self.assertIn('detail_frame = ttk.Frame(detail_content, style="ResultBody.TFrame")', self.APP_SOURCE)

    def test_inspection_detail_action_is_right_aligned_beside_primary_rows(self) -> None:
        self.assertIn('detail_content.pack(side="left", fill="x", expand=True)', self.APP_SOURCE)
        self.assertIn('detail_button.pack(side="right", anchor="center", padx=(12, 0))', self.APP_SOURCE)
        self.assertIn('style="Detail.TButton"', self.APP_SOURCE)
        self.assertNotIn('detail_button.pack(anchor="w"', self.APP_SOURCE)

    def test_preview_and_filename_spacing_are_grouped(self) -> None:
        self.assertIn('pady=(0, 4), before=self.file_name_label', self.APP_SOURCE)
        self.assertIn('self.supported_formats_label.pack(anchor="w", pady=(2, 14))', self.APP_SOURCE)

    def test_preview_size_preserves_aspect_ratio_without_upscaling(self) -> None:
        self.assertEqual((220, 150), PREVIEW_MAX_SIZE)
        self.assertEqual((200, 150), preview_size_for(4000, 3000))
        self.assertEqual((100, 150), preview_size_for(2000, 3000))
        self.assertEqual((120, 90), preview_size_for(120, 90))

    def test_initial_window_and_footer_keep_disclaimers_in_primary_window(self) -> None:
        self.assertEqual((620, 720), INITIAL_WINDOW_SIZE)
        self.assertIn('footer.pack(side="bottom"', self.APP_SOURCE)
        self.assertIn("text=LOCAL_PROCESSING_NOTE", self.APP_SOURCE)
        self.assertIn("text=DISCLAIMER", self.APP_SOURCE)
        self.assertNotIn("ttk.Scrollbar", self.APP_SOURCE)
        self.assertNotIn("content_scrollbar", self.APP_SOURCE)

    def test_windows_user_launcher_and_child_processes_are_consoleless(self) -> None:
        launcher = (PROJECT / "Shirushi.vbs").read_text(encoding="utf-8")
        creator_e2e = (PROJECT / "scripts/creator_e2e.py").read_text(encoding="utf-8")
        creator_verify = (PROJECT / "scripts/creator_verify.py").read_text(encoding="utf-8")
        manifest_audit = (PROJECT / "scripts/manifest_claim_audit.py").read_text(encoding="utf-8")
        self.assertIn("pythonw.exe", launcher)
        self.assertIn("shell.Run command, 1, False", launcher)
        self.assertIn("creationflags=WINDOWS_NO_WINDOW", creator_e2e)
        self.assertIn("creationflags=WINDOWS_NO_WINDOW", creator_verify)
        self.assertNotIn('which("pwsh")', creator_verify)
        self.assertNotIn('which("pwsh")', manifest_audit)
        self.assertIn("SetCurrentProcessExplicitAppUserModelID", self.APP_SOURCE)

    def test_local_processing_copy_separates_image_privacy_from_setup_network(self) -> None:
        self.assertEqual(
            "画像ファイル自体は外部サービスへ送信しません。\n"
            "検証に必要なコンポーネントの取得や、画像に外部参照情報が含まれる場合は通信が発生することがあります。",
            LOCAL_PROCESSING_NOTE,
        )
        self.assertIn("画像ファイル自体は外部サービスへ送信しません。", LOCAL_PROCESSING_NOTE)

    def test_model_acquisition_network_copy_preserves_privacy_boundary(self) -> None:
        message = message_for_result(
            {"status": "WRITE_FAILED", "errorCode": "MODEL_NETWORK_UNAVAILABLE"}
        )
        self.assertEqual("初回準備を完了できませんでした", message.heading)
        self.assertIn("インターネット接続", message.detail)
        self.assertIn("同じ操作をもう一度", message.detail)
        self.assertIn("画像ファイル自体は送信していません", message.detail)
        self.assertNotIn("TrustMark", message.detail)
        self.assertNotIn(".ckpt", message.detail)

    def test_model_preparation_uses_existing_processing_surface(self) -> None:
        source = (PROJECT / "src/gui/app.py").read_text(encoding="utf-8")
        self.assertIn('self.status_heading.set("初回準備中")', source)
        self.assertIn(
            'self.status_detail.set("初回のみ時間がかかる場合があります。しばらくお待ちください。")',
            source,
        )
        self.assertNotIn("MODEL_REMOTE_HOST", source)
        self.assertIn("コンポーネントの取得", LOCAL_PROCESSING_NOTE)
        self.assertIn("外部参照情報", LOCAL_PROCESSING_NOTE)

    def test_font_and_icon_configuration_is_replaceable(self) -> None:
        self.assertEqual("Yu Gothic UI", WINDOWS_FONT_CANDIDATES[0])
        self.assertEqual("Hiragino Sans", MACOS_FONT_CANDIDATES[0])
        self.assertEqual("TkDefaultFont", FALLBACK_FONT)
        self.assertEqual(Path("assets/app_icon.png"), APP_ICON_PATH.relative_to(PROJECT))
        self.assertEqual(Path("assets/app_icon.ico"), APP_ICON_ICO_PATH.relative_to(PROJECT))
        self.assertIn("self.root.iconphoto(True, self._icon_image)", self.APP_SOURCE)
        self.assertIn("self.root.iconbitmap(default=str(APP_ICON_ICO_PATH))", self.APP_SOURCE)
        self.assertIn("self.root.after_idle(self._set_mapped_window_icon)", self.APP_SOURCE)
        self.assertIn("self.root.iconphoto(False, self._icon_image)", self.APP_SOURCE)
        self.assertIn("self.root.iconbitmap(str(APP_ICON_ICO_PATH))", self.APP_SOURCE)
        self.assertNotIn("_set_optional_icon", self.APP_SOURCE)


if __name__ == "__main__":
    unittest.main()
