"""Provisional product copy and replaceable visual asset locations."""

from runtime_paths import resolve_runtime_paths

_RUNTIME_PATHS = resolve_runtime_paths()

# Provisional until the Owner completes naming review.
PRODUCT_NAME = "しるし"
PRODUCT_NAME_EN = "Shirushi"
PRODUCT_DESCRIPTION = "創作しるし"
PRODUCT_NAME_IS_PROVISIONAL = True
TAGLINE = "作品に利用意思を残す"

RIGHTS_SECTION = "権利設定"
RIGHTS_LABEL = "AIによる学習・生成利用を希望しない"
RIGHTS_DESCRIPTION = "AI利用に関する意思を画像に記録します"
RIGHTS_USAGE_NOTICE = "必要な権利または許諾を持つ画像に使用してください。"

IMAGE_PICKER_LABEL = "画像を選ぶ"
SUPPORTED_FORMATS_LABEL = "PNG・JPEGに対応しています。"
ACTION_LABEL = "しるしを付ける"
INSPECT_LABEL = "しるしを確認する"
CANCEL_LABEL = "キャンセル"

DISCLAIMER = (
    "AI利用に関する意思表示を、対応システムが読み取れる形で画像に追加します。\n"
    "すべてのAIやサービスがこの指定を尊重することを保証するものではありません。\n"
    "しるしは、画像の著作権者・権利者本人であることを確認または証明するものではありません。"
)
LOCAL_PROCESSING_NOTE = (
    "画像ファイル自体は外部サービスへ送信しません。\n"
    "検証に必要なコンポーネントの取得や、画像に外部参照情報が含まれる場合は通信が発生することがあります。"
)

# Owner-approved Shirushi application icon assets.
APP_ICON_PATH = _RUNTIME_PATHS.app_icon
APP_ICON_ICO_PATH = _RUNTIME_PATHS.app_icon_ico
