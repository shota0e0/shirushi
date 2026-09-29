export const SUPPORTED_LOCALES = ["ja", "en", "zh-CN", "zh-TW", "ko"];

export const MESSAGES = {
  ja: {
    noMark: "未設定",
    languageLabel: "表示言語", detailsLabel: "詳細", eyebrow: "あなたの作品に、あなたの意思を", headline: "作品に、あなたのしるしを。",
    emptyTitle: "画像を選んで始める", emptyBody: "画像はアップロードせず、この端末内のPreviewだけに使います。", selectImage: "画像を選ぶ", changeImage: "画像を変更",
    yourMark: "あなたのしるし", editMark: "変更", addMark: "しるしを付ける", addAgain: "もう一度見る", verifyMark: "しるしを確認", verifyAgain: "もう一度確認",
    capabilityUnavailable: "Core未接続のため、ファイルへの追加と確認はまだ利用できません。", previewReady: "開発用motion previewの準備ができました", statusReady: "画像を選ぶとPreviewできます", statusSelected: "ローカルPreviewを表示中",
    statusAddingPreview: "追加motionをPreview中…", statusAddPreviewDone: "追加motion preview完了（ファイル未処理）", statusVerifyPreview: "固定fixtureをPreview中…", statusVerifyPreviewDone: "確認motion preview完了（実検証ではありません）",
    rightsIntent: "AI学習・生成利用を希望しない", footerNormal: "Shared Web foundation — ローカルPreviewのみ。ファイル処理・埋め込み・readbackは未接続です。",
    devBanner: "非本番・開発用motion preview", devProvenance: "Fixture provenance: F1 approved isolated development fixture", fixtureLabel: "開発fixture", fixtureTyped: "文字", fixtureHandwritten: "手書き",
    markDialogTitle: "あなたのしるし", close: "閉じる", typedMark: "名前で作る", handwrittenMark: "手書きで作る", displayName: "表示する名前", typedHelp: "この変更はセッション中だけ保持されます。", handwrittenHelp: "線の描画領域をvisual metadataとして保持します。", drawHere: "ここに描く", redraw: "書き直す", identityBoundary: "本人証明・法的署名・著作権証明ではありません。", cancel: "キャンセル", saveMark: "このしるしを使う",
    detailsTitle: "現在の機能境界", detailsBody: "ローカル画像Previewとセッション内しるし編集のみ対応。Core Add、Verify、C2PA readback、永続化は未実装です。", done: "閉じる", selectedImageAlt: "選択したローカル画像のPreview", typedRequired: "表示名を入力してください", drawingRequired: "少なくとも1本の線を描いてください", imageLoadFailed: "画像を読み込めませんでした", motionAdd: "追加Preview", motionVerify: "確認Preview", storedMarkSource: "対象fixtureに保存されたしるし",
  },
  en: {
    noMark: "Not set",
    languageLabel: "Display language", detailsLabel: "Details", eyebrow: "Your work, your intent", headline: "Leave your mark on your work.",
    emptyTitle: "Choose an image to begin", emptyBody: "The image is not uploaded; it is used only for a local preview on this device.", selectImage: "Choose image", changeImage: "Change image",
    yourMark: "Your mark", editMark: "Change", addMark: "Add my mark", addAgain: "Watch again", verifyMark: "Check a mark", verifyAgain: "Check again",
    capabilityUnavailable: "Core is not connected, so adding to or checking a file is not available yet.", previewReady: "Development motion preview is ready", statusReady: "Choose an image for a local preview", statusSelected: "Showing a local preview",
    statusAddingPreview: "Previewing add motion…", statusAddPreviewDone: "Add motion preview complete (no file processing)", statusVerifyPreview: "Previewing a fixed fixture…", statusVerifyPreviewDone: "Verify motion preview complete (not real verification)",
    rightsIntent: "No AI training or generative use", footerNormal: "Shared Web foundation — local preview only. File processing, embedding, and readback are not connected.",
    devBanner: "NON-PRODUCTION DEVELOPMENT MOTION PREVIEW", devProvenance: "Fixture provenance: F1 approved isolated development fixture", fixtureLabel: "Development fixture", fixtureTyped: "Typed", fixtureHandwritten: "Handwritten",
    markDialogTitle: "Your mark", close: "Close", typedMark: "Use my name", handwrittenMark: "Draw by hand", displayName: "Display name", typedHelp: "This edit lasts only for this session.", handwrittenHelp: "The drawing area is retained only as visual view-model metadata.", drawHere: "Draw here", redraw: "Redraw", identityBoundary: "Not verified identity, a legal signature, or proof of copyright.", cancel: "Cancel", saveMark: "Use this mark",
    detailsTitle: "Current capability boundary", detailsBody: "Only local image preview and session-only mark editing are available. Core Add, Verify, C2PA readback, and persistence are not implemented.", done: "Done", selectedImageAlt: "Preview of the selected local image", typedRequired: "Enter a display name", drawingRequired: "Draw at least one stroke", imageLoadFailed: "The image could not be loaded", motionAdd: "Add preview", motionVerify: "Verify preview", storedMarkSource: "Mark stored in the target fixture",
  },
  "zh-CN": {
    noMark: "未设置",
    languageLabel: "显示语言", detailsLabel: "详细信息", eyebrow: "为你的作品，留下你的意愿", headline: "在作品上，留下你的印记。", emptyTitle: "选择图片后开始", emptyBody: "图片不会上传，只用于此设备上的本地预览。", selectImage: "选择图片", changeImage: "更换图片", yourMark: "你的印记", editMark: "更改", addMark: "添加印记", addAgain: "再次播放", verifyMark: "检查印记", verifyAgain: "再次检查", capabilityUnavailable: "Core尚未连接，因此暂时无法写入或检查文件。", previewReady: "开发motion预览已就绪", statusReady: "选择图片以进行本地预览", statusSelected: "正在显示本地预览", statusAddingPreview: "正在预览添加motion…", statusAddPreviewDone: "添加motion预览完成（未处理文件）", statusVerifyPreview: "正在预览固定fixture…", statusVerifyPreviewDone: "检查motion预览完成（非真实验证）", rightsIntent: "不希望用于AI训练或生成", footerNormal: "Shared Web foundation — 仅本地预览；未连接文件处理、嵌入与readback。", devBanner: "非生产开发motion预览", devProvenance: "Fixture provenance: F1 approved isolated development fixture", fixtureLabel: "开发fixture", fixtureTyped: "文字", fixtureHandwritten: "手写", markDialogTitle: "你的印记", close: "关闭", typedMark: "使用姓名", handwrittenMark: "手写绘制", displayName: "显示名称", typedHelp: "更改只在本次会话中保留。", handwrittenHelp: "绘图区仅作为视觉view-model metadata保留。", drawHere: "在此绘制", redraw: "重新绘制", identityBoundary: "不是身份验证、法律签名或版权证明。", cancel: "取消", saveMark: "使用此印记", detailsTitle: "当前功能边界", detailsBody: "仅支持本地图片预览与会话内印记编辑。Core Add、Verify、C2PA readback和持久化尚未实现。", done: "完成", selectedImageAlt: "所选本地图片的预览", typedRequired: "请输入显示名称", drawingRequired: "请至少绘制一笔", imageLoadFailed: "无法读取图片", motionAdd: "添加预览", motionVerify: "检查预览", storedMarkSource: "目标fixture中保存的印记",
  },
  "zh-TW": {
    noMark: "未設定",
    languageLabel: "顯示語言", detailsLabel: "詳細資訊", eyebrow: "為你的作品，留下你的意願", headline: "在作品上，留下你的印記。", emptyTitle: "選擇圖片後開始", emptyBody: "圖片不會上傳，只用於此裝置上的本機預覽。", selectImage: "選擇圖片", changeImage: "更換圖片", yourMark: "你的印記", editMark: "更改", addMark: "加上印記", addAgain: "再次播放", verifyMark: "檢查印記", verifyAgain: "再次檢查", capabilityUnavailable: "Core尚未連接，因此目前無法寫入或檢查檔案。", previewReady: "開發motion預覽已就緒", statusReady: "選擇圖片以進行本機預覽", statusSelected: "正在顯示本機預覽", statusAddingPreview: "正在預覽加入motion…", statusAddPreviewDone: "加入motion預覽完成（未處理檔案）", statusVerifyPreview: "正在預覽固定fixture…", statusVerifyPreviewDone: "檢查motion預覽完成（非真實驗證）", rightsIntent: "不希望用於AI訓練或生成", footerNormal: "Shared Web foundation — 僅本機預覽；未連接檔案處理、嵌入與readback。", devBanner: "非正式開發motion預覽", devProvenance: "Fixture provenance: F1 approved isolated development fixture", fixtureLabel: "開發fixture", fixtureTyped: "文字", fixtureHandwritten: "手寫", markDialogTitle: "你的印記", close: "關閉", typedMark: "使用姓名", handwrittenMark: "手寫繪製", displayName: "顯示名稱", typedHelp: "變更只在本次工作階段保留。", handwrittenHelp: "繪圖區只作為視覺view-model metadata保留。", drawHere: "在此繪製", redraw: "重新繪製", identityBoundary: "不是身分驗證、法律簽名或著作權證明。", cancel: "取消", saveMark: "使用此印記", detailsTitle: "目前功能邊界", detailsBody: "僅支援本機圖片預覽與工作階段內印記編輯。Core Add、Verify、C2PA readback與持久化尚未實作。", done: "完成", selectedImageAlt: "所選本機圖片的預覽", typedRequired: "請輸入顯示名稱", drawingRequired: "請至少繪製一筆", imageLoadFailed: "無法讀取圖片", motionAdd: "加入預覽", motionVerify: "檢查預覽", storedMarkSource: "目標fixture中儲存的印記",
  },
  ko: {
    noMark: "설정되지 않음",
    languageLabel: "표시 언어", detailsLabel: "세부 정보", eyebrow: "내 작품에, 나의 의사를", headline: "작품에 나만의 표시를 남기세요.", emptyTitle: "이미지를 선택해 시작하기", emptyBody: "이미지는 업로드되지 않으며 이 기기의 로컬 미리보기에만 사용됩니다.", selectImage: "이미지 선택", changeImage: "이미지 변경", yourMark: "나의 표시", editMark: "변경", addMark: "표시 남기기", addAgain: "다시 보기", verifyMark: "표시 확인", verifyAgain: "다시 확인", capabilityUnavailable: "Core가 연결되지 않아 파일 추가 및 확인은 아직 사용할 수 없습니다.", previewReady: "개발 motion 미리보기 준비 완료", statusReady: "로컬 미리보기를 위해 이미지를 선택하세요", statusSelected: "로컬 미리보기 표시 중", statusAddingPreview: "추가 motion 미리보기 중…", statusAddPreviewDone: "추가 motion 미리보기 완료(파일 처리 없음)", statusVerifyPreview: "고정 fixture 미리보기 중…", statusVerifyPreviewDone: "확인 motion 미리보기 완료(실제 검증 아님)", rightsIntent: "AI 학습 및 생성 이용을 원하지 않음", footerNormal: "Shared Web foundation — 로컬 미리보기 전용. 파일 처리, 삽입, readback은 연결되지 않았습니다.", devBanner: "비프로덕션 개발 motion 미리보기", devProvenance: "Fixture provenance: F1 approved isolated development fixture", fixtureLabel: "개발 fixture", fixtureTyped: "문자", fixtureHandwritten: "손글씨", markDialogTitle: "나의 표시", close: "닫기", typedMark: "이름으로 만들기", handwrittenMark: "직접 그리기", displayName: "표시 이름", typedHelp: "변경 사항은 이번 세션 동안만 유지됩니다.", handwrittenHelp: "그리기 영역은 시각적 view-model metadata로만 유지됩니다.", drawHere: "여기에 그리기", redraw: "다시 그리기", identityBoundary: "본인 인증, 법적 서명 또는 저작권 증명이 아닙니다.", cancel: "취소", saveMark: "이 표시 사용", detailsTitle: "현재 기능 경계", detailsBody: "로컬 이미지 미리보기와 세션 내 표시 편집만 지원합니다. Core Add, Verify, C2PA readback 및 영구 저장은 구현되지 않았습니다.", done: "완료", selectedImageAlt: "선택한 로컬 이미지 미리보기", typedRequired: "표시 이름을 입력하세요", drawingRequired: "한 개 이상의 획을 그려 주세요", imageLoadFailed: "이미지를 불러오지 못했습니다", motionAdd: "추가 미리보기", motionVerify: "확인 미리보기", storedMarkSource: "대상 fixture에 저장된 표시",
  },
};

export function normalizeLocale(value) {
  if (SUPPORTED_LOCALES.includes(value)) return value;
  const lower = value?.toLowerCase() || "";
  if (lower.startsWith("zh-tw") || lower.startsWith("zh-hk")) return "zh-TW";
  if (lower.startsWith("zh")) return "zh-CN";
  if (lower.startsWith("ko")) return "ko";
  if (lower.startsWith("en")) return "en";
  return "ja";
}

export function translator(locale) {
  const selected = MESSAGES[normalizeLocale(locale)] || MESSAGES.ja;
  return (key) => selected[key] ?? MESSAGES.ja[key] ?? key;
}
