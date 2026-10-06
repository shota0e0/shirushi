# Shirushi 第三者コンポーネントに関する表示

## v0.2 — 公開前のnotice対象と未完事項

v0.2はRust Desktop/helperの公開candidateを準備中です。Development artifactの3ファイルauditは、そのバイナリに静的／間接に含まれる第三者componentのnotice充足を証明しません。現時点でDistribution Compliance READYとはしません。

最終配布物のsource revision・Cargo locks・toolchain・ファイルhashと実際のcomponent構成に対して、少なくとも以下を対応付けます。

- Desktop/Tauriと実際にリンク／生成された依存component、WebView2 SDK/runtime関連の適用条件。
- Rust helperのc2pa-rs `0.85.0`、native crypto/image依存、実際に含まれる各componentのlicense／notice。
- c2pa由来MPL対象sourceとsource-availability条件。既存のexact source evidenceは[凍結記録](packaging/license_sources/inspection-helper/c2pa-mpl/README.md)にありますが、そのsubsetだけで最終配布上の充足を断定しません。
- UIに含まれるUnicode 16.0データとUnicode License v3。不要なdot fontは製品commit／packageへ追加しません。
- 同梱する場合の正確なMicrosoft VC/WebView2 offline candidateと再配布条件。現在はCANDIDATEで、開発用のidentity確認は公開再配布承認ではありません。
- v0.2 Previewで採用する既存の公開テスト署名資格情報の出所／noticeとPreview用途。第三者も使用できるテスト資格情報であり、Production/private署名情報や作者本人性として説明しません。

旧v0.1のPython／PyInstaller／c2patool／TrustMark一覧を、v0.2の実配布構成として流用しません。最終artifactがないため、除外／同梱と適用noticeの確定・完成は未実施です。下記はv0.1と既存repository素材の履歴です。

---

この文書は、Shirushi v0.1で使用する第三者コンポーネントについて、現在の配布上の取り扱いを記録するものです。トップレベルのMIT Licenseは、`Copyright (c) 2026 しょたお`の下で公開するShirushi独自のソースコードに適用されます。第三者のソフトウェア、バイナリ、モデル、証明書、その他の素材に適用されるライセンスや表示を置き換えたり、別のライセンスへ変更したりするものではありません。各コンポーネントには、それぞれの開発元が定めるライセンスと表示が適用されます。

必要な表示は、実際の配布パッケージに含まれる内容によって決まります。リリース候補では、実ファイル一覧とSHA-256を作成し、その内容に対応する開発元のライセンスおよび表示を`LICENSES/`へ収録します。

## c2patool / c2pa-rs

| 項目 | 現在の記録 |
| --- | --- |
| コンポーネント | `c2pa-rs`プロジェクトの`c2patool` |
| バージョン | `0.26.60` |
| 開発元 | `contentauth/c2pa-rs`、公式タグ`c2patool-v0.26.60` |
| 開発元ライセンス | `MIT OR Apache-2.0` |
| Shirushiでの用途 | C2PAマニフェストの読み取り、作成、検証に使用するWindows向けコマンドラインバイナリを同梱 |
| バイナリ再配布の根拠 | バージョンを固定した開発元タグにMITとApache-2.0の許諾があります。リリース候補には両方の開発元ライセンス本文を保持します。 |
| ライセンス本文の取得元 | 公式タグ`c2patool-v0.26.60`の`LICENSE-MIT`と`LICENSE-APACHE` |
| タグ上のNOTICE | 開発元タグに`NOTICE`ファイルは見つかりませんでした。 |
| 公式SBOM | Windows向け公式リリースのSPDX 2.3 SBOMを`LICENSES/c2patool/`へ保持します。 |

`c2patool`のライセンスは、ShirushiのMIT Licenseへ置き換えられるものではありません。公式SBOMに記録された依存コンポーネントのライセンスと表示は、実際の配布内容から確定します。この文書で465パッケージすべての表示を手作業で再構成するものではありません。

## TrustMark

| 項目 | 現在の記録 |
| --- | --- |
| コンポーネント | Adobe TrustMark Pythonパッケージ |
| バージョン | `0.9.0` |
| 開発元 | `adobe/trustmark` |
| Pythonパッケージのライセンス | インストール済みパッケージのメタデータと同梱`LICENSE`ファイルに記録されたMIT |
| Shirushiでの用途 | 開発元のエンコード・デコード実装を、ソースを分岐せずに使用 |
| モデルファイル | Shirushiの配布物には同梱しない |
| モデルの取得 | 初回利用時に公式の配布元から自動取得 |
| ミラーまたは再配布 | Shirushiはモデルファイルをミラーせず、再配布しない |

パッケージコードのライセンスだけでは、別途取得するモデルリソースのライセンス条件は確定しません。これらのリソースは、バージョンごとに区別して記録します。

## TrustMarkモデルリソース

次のTrustMark 0.9.0 variant Pリソースは、`config/trustmark-models-v0.9.0.json`で管理します。

- `trustmark_P.yaml`
- `decoder_P.ckpt`
- `encoder_P.ckpt`

これらはShirushiの配布物に含めません。初回利用時に、手動でのモデル準備を必要とせず、公式の配布元から次の場所へ取得します。

`%LOCALAPPDATA%\Shirushi\models\trustmark\0.9.0\P`

Shirushiは取得したファイルのバイト列を変更しません。実行時には、バージョン、サイズ、開発元MD5、SHA-256を固定して確認します。配布検査では、既知のファイル名またはハッシュが配布対象に含まれている場合に処理を停止します。

バージョンを固定した調査記録からは、これらのモデルリソースを同梱またはミラーするための明示的な許諾を確認できていません。パッケージのMIT Licenseが、取得したリソースにも自動的に適用されるとは扱いません。

## Pythonによる署名検証の依存パッケージ

PowerShell 7に依存する署名検証を次のPythonパッケージへ置き換えました。バージョンとライセンスファイルは、リリース候補に使用したWindows向け配布物と実ファイル一覧から確認しています。

| コンポーネント | バージョン | 開発元 | 適用ライセンス | 配布パッケージに含める場合の記録 |
| --- | --- | --- | --- | --- |
| `cryptography` | `50.0.1` | `pyca/cryptography` | `Apache-2.0 OR BSD-3-Clause` | `LICENSE`、`LICENSE.APACHE`、`LICENSE.BSD`、同梱SBOMを`LICENSES/cryptography/`へ保持します。 |
| `cffi` | `2.1.1` | `python-cffi/cffi` | `MIT-0` | 開発元`LICENSE`を`LICENSES/cffi/`へ保持します。 |
| `pycparser` | `3.0` | `eliben/pycparser` | `BSD-3-Clause` | 著作権表示、条件、免責条項を含む開発元`LICENSE`を`LICENSES/pycparser/`へ保持します。 |

これらの配布物のライセンス本文を、ShirushiのMIT Licenseとして書き換えたり、別のライセンスへ変更したりしていません。最終的なライセンスの充足判断は、実際のリリース候補（RC）に含まれるファイルに対して行います。

## Python関連コンポーネント一覧

次の一覧は、現在のWindows向けリリース候補に含まれる主要コンポーネントです。補助Pythonパッケージを含む全一覧は、配布物の`LICENSES/PYTHON_COMPONENTS.md`と`LICENSES/PYTHON_COMPONENTS.json`へ記録します。

| コンポーネント | 確認したバージョン | 現在の役割 | 配布物でのライセンス表示 |
| --- | --- | --- | --- |
| Python | `3.12.10` | アプリケーションの実行環境 | バージョンに対応するPythonのライセンスを`LICENSES/Python/`へ保持します。 |
| Tcl/Tk | `8.6` / `8.6` | Tkinter GUIの実行環境 | 最終パッケージに含まれるTcl/Tkファイルを特定し、各バージョンに対応する表示を含めます。 |
| PyTorch | `2.14.0` | TrustMarkモデルの実行環境 | 配布物に含まれる開発元および第三者ライセンスファイル群を`LICENSES/PyTorch/`へ保持します。 |
| torchvision | `0.29.0` | TrustMarkの画像・モデル処理 | 開発元ライセンスを`LICENSES/torchvision/`へ保持します。 |
| NumPy | `1.26.4` | 画像・モデル処理の数値計算 | 実際に同梱するwheelの内容に対応するライセンスと、同梱ライブラリの表示を保持します。 |
| Pillow | `12.3.0` | PNG・JPEG画像の処理 | 開発元ライセンスを`LICENSES/Pillow/`へ保持します。 |

## リポジトリ内テスト素材の再配布監査

画像やテスト素材は、リポジトリに保存されているだけでShirushi所有またはMIT Licenseの対象になるものではありません。

| 素材のグループ | 根拠 | 公開時の状態 | 必要な対応 |
| --- | --- | --- | --- |
| `testdata/e2e/clean_fixture.png`と`.jpg` | 外部画像を使わず、プロジェクトのスクリプトから決定的に生成 | 公開リポジトリへ収録可能 | 生成元の記録をスクリプトとともに保持します。 |
| `testdata/tamper/*.png` | 決定的に生成したプロジェクトのテスト画像から、テストスクリプトで派生 | 公開リポジトリへ収録可能 | テスト専用の派生物として、生成元の記録をスクリプトとともに保持します。 |
| `testdata/jpeg_realworld/*.jpg` | `recurser/exif-orientation-examples`から派生。ローカルにMIT本文とクレジットを保持 | 表示を伴って公開リポジトリへ収録可能 | 既存の著作権表示と許諾表示をファイルとともに配布します。 |
| `tests/fixtures/inspection/` | 記録済みのプロジェクト用スクリプトで、決定的な合成テスト画像から生成。外部画像素材は未使用 | 公開リポジトリへ収録可能 | マニフェストと生成スクリプトをテスト画像とともに保持します。 |

## ソースコードのライセンス表示

### Personal Mark v2 の Unicode 16.0 データ

F2BのNFC・文字属性判定ではUnicode ConsortiumのUCD 16.0.0から機械生成した固定テーブルを使用します。ホストPython/JavaScriptのUnicodeバージョンには依存しません。テーブル・公式normalization testの派生fixtureはUnicode License v3（Unicode-3.0）の対象であり、ShirushiのMIT Licenseへ変更しません。

- Pythonデータ: `src/data/personal_mark_unicode16.json`、ライセンス本文: `src/data/Unicode-LICENSE.txt`。
- Webデータ: `web/personal-mark-v2/unicode16-data.js`、ライセンス本文: 同directoryの`Unicode-LICENSE.txt`。
- 派生テストfixture: `tests/fixtures/personal_mark_unicode16_normalization.json`、ライセンス本文: `tests/fixtures/Unicode-LICENSE.txt`。
- 生成手順: `scripts/generate_personal_mark_unicode16.py`。公式取得元URLとSHA-256を固定テーブルおよび生成scriptに記録します。アプリ実行・通常テストではネットワーク取得しません。
- 取得元: <https://www.unicode.org/Public/16.0.0/ucd/>、ライセンス: <https://www.unicode.org/license.txt>。

これはリポジトリ内F2Bデータの表示です。配布パッケージへの組込み・release検証を実施済みとはしません。

リポジトリでは、トップレベルの`LICENSE`をShirushi独自ソースのライセンス表示として使用します。現行の運用では、すべてのソースファイルへ個別のヘッダーを追加する必要はないとしています。SPDXヘッダーは、今後の配布または開発参加の方式で必要になった場合に検討します。

## 配布パッケージの確認項目

現在の配布パッケージについて、次の項目を確認対象としています。

1. 実際のバイナリ構成を一覧化する。
2. `c2patool` / `c2pa-rs`へ適用するライセンス本文を選び、同梱する。
3. 公式SBOMと同梱パッケージのメタデータから、必要な表示を組み立てる。
4. TrustMarkモデルファイルと既知のハッシュが含まれていないことを確認する。
5. 再配布根拠が不明、または公開前に除外が必要と記録されたテスト素材をすべて解決する。
6. トップレベルの`LICENSE`に`Copyright (c) 2026 しょたお`が保持されていることを確認する。
7. 同梱した`cryptography`、`cffi`、`pycparser`の開発元ライセンス本文を`LICENSES/`へコピーし、同梱されたネイティブ依存コンポーネントを確認する。
