# Shirushi v0.1 Preview Windowsビルド手順

この文書は、公開リポジトリのソースからShirushi v0.1 PreviewのWindows向け配布物を組み立てるための手順です。対象はWindows x64とCPython 3.12.10です。通常利用の手順ではありません。

## 1. 前提環境

- Windows x64
- CPython 3.12.10（64 bit）
- インターネット接続
- 書き込み可能なリポジトリの作業コピー

CUDA環境は不要です。PyTorchとtorchvisionには、公式CPU wheel indexで配布される固定バージョンを使用します。

TrustMark 0.9.0のPythonパッケージは実行環境へ導入しますが、モデルファイルはリポジトリにも配布物にも含めません。モデルは製品の初回利用時に、固定された公式配布元から取得されます。

## 2. 仮想環境の作成

PowerShellでリポジトリのルートへ移動し、ビルド専用の仮想環境を作成します。

```powershell
py -3.12 -m venv .venv-release
.\.venv-release\Scripts\python.exe -m pip install pip==25.0.1
```

次の確認結果が`Python 3.12.10`でなければ、依存パッケージを導入せず正しいPythonを用意してください。

```powershell
.\.venv-release\Scripts\python.exe --version
```

## 3. 依存パッケージの導入

実行時、ビルド時、テスト時の契約を分けています。`requirements-release-test.txt`には、標準ライブラリの`unittest`以外の追加テスト依存関係はありません。

```powershell
.\.venv-release\Scripts\python.exe -m pip install -r requirements-release-runtime.txt
.\.venv-release\Scripts\python.exe -m pip install -r requirements-release-build.txt
.\.venv-release\Scripts\python.exe -m pip install -r requirements-release-test.txt
```

`requirements-release-runtime.txt`にある`+cpu`付きの固定指定とPyTorch公式CPU wheel indexを変更しないでください。

## 4. ビルド環境の確認

```powershell
.\.venv-release\Scripts\python.exe -m pip check
.\.venv-release\Scripts\python.exe scripts\check_release_build_environment.py --project-root .
```

環境チェッカーは、PythonとCPUアーキテクチャ、固定したPythonパッケージ、CPU版PyTorch、同梱する`c2patool.exe`のバージョンとSHA-256を確認します。いずれかが一致しない場合はビルドへ進まないでください。

## 5. テスト

```powershell
.\.venv-release\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
```

公開リポジトリへ含めるテストの全件が成功してからビルドします。内部研究・内部QA用のスクリプトはこの公開ビルド契約に含めません。

## 6. PyInstallerビルド

既存のspecを使用し、ソースツリーとは別の出力先へone-directory形式で生成します。

```powershell
.\.venv-release\Scripts\python.exe -m PyInstaller --noconfirm --clean `
  --distpath output\public-build\dist `
  --workpath output\public-build\work `
  packaging\Shirushi.spec
```

生成されるアプリケーション本体は次の場所です。

```text
output\public-build\dist\Shirushi\Shirushi.exe
```

## 7. 公開文書・ライセンス・実ファイル一覧の組み立て

```powershell
.\.venv-release\Scripts\python.exe scripts\assemble_release_candidate.py `
  --project-root . `
  --payload-root output\public-build\dist\Shirushi `
  --inventory-json output\public-build\payload_inventory.json `
  --inventory-csv output\public-build\payload_inventory.csv
```

この処理は公開文書と第三者ライセンスを配布ディレクトリへ同期し、実ファイル一覧とSHA-256を生成します。TrustMarkモデルの既知のファイル名またはハッシュ、秘密鍵に使われる拡張子、ローカル生成物のディレクトリが配布対象に含まれる場合は失敗します。

## 8. 配布前の確認

- `pip check`と環境チェッカーが成功している
- 公開テストが全件成功している
- `payload_inventory.json`と`payload_inventory.csv`が生成されている
- `Shirushi.exe`、`runtime/`、`docs/`、`LICENSES/`が同じ配布ディレクトリにある
- TrustMarkモデルファイルが0件である
- 本番用の秘密鍵とProduction Trust用の資格情報が含まれていない
- 配布候補に対するOwner UI Reviewとライセンス監査が完了している

この手順はビルドと検証の方法を定めるものです。公開、タグ作成、GitHub Release作成は別のOwner承認後に行います。
