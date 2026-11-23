# Things Windows 非公式クライアント

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md)

公開コミュニティプロトコルを通じて Things Cloud を読み書きする独立した
Windows デスクトップクライアントです。macOS、iPhone、iPad の Things と
タスクを同期できます。

> **非公式プロジェクト:** 本プロジェクトは Cultured Code と提携しておらず、
> 同社によるサポートや承認も受けていません。Things は Cultured Code の
> 商標です。非公開の Cloud プロトコルを利用するため、互換性とアカウントに
> 関するリスクがあります。重要なデータは事前にバックアップしてください。

## 機能

- pywebview と Microsoft Edge WebView2 によるネイティブデスクトップ画面。
- Things Cloud の変更を取得し、Windows での編集を同じ history に送信。
- タイトル、メモ、期限、タグ、プロジェクト／エリア所属を含むタスクと
  プロジェクトの作成・編集。
- Inbox、Today、Upcoming、Calendar、Anytime、Someday、Logbook、Trash、
  プロジェクトの各ビュー。
- 予定日と期限を表示する月間カレンダー。日付を選択してタスクを確認可能。
- カレンダーで選択した日付へのタスク作成、詳細画面での予定日変更。
- どこからでも `Ctrl+T` で新規タスク画面を開き、Today に追加。
- 完了、復元、キャンセル、ゴミ箱への移動、ゴミ箱からの復元、並べ替え。
- ローカルディスクキャッシュ、永続的なオフライン書き込みキュー、自動再試行、
  競合解決画面。
- Windows DPAPI で保護された認証情報と、任意のローカル PIN 検証。
- 添付ファイルは Windows にローカル保存し、メモには添付マーカーのテキスト
  のみを同期。

## 未検証事項

このリリースでは、実際の Things Cloud アカウントへの書き込みと iPhone との
往復同期は検証されていません。現在の検証範囲は、ローカル HTTP API、
永続キュー、競合ポリシー、およびシミュレートされた Things Cloud に対する
双方向コミットです。

以下は互換性のための設計であり、相互運用性を保証するものではありません。

- コミュニティ Go SDK のルールに従って完全な Task7 作成ペイロードを生成。
- コミュニティ SDK で検証済みの `ThingsMac/32209501` 互換 ID と
  schema を使用。
- コミット成功後、同じ history を読む macOS/iOS クライアントから Windows
  での変更を参照できる想定。

実アカウントで未検証の項目には、既存アカウントでのログイン、Apple
アカウントのセキュリティ通知、二要素認証、Things/iOS のバージョン差異、
繰り返しタスク、Cloud のレート制限があります。初回検証にはテスト
アカウントまたは復元可能なバックアップを使用してください。

## 動作要件

- Windows 10/11 x64。
- ソースから実行する場合は Python 3.10 以降。
- Microsoft Edge WebView2 Runtime（通常は Windows 10/11 に含まれます）。
- 有効な Things Cloud アカウント。

インストールと起動:

```powershell
python -m pip install -r requirements-dev.txt
python main_window.py
```

`启动.bat` をダブルクリックすることもできます。`dist` に v3 の実行ファイルが
ある場合は EXE を優先し、ない場合は `main_window.py` を起動します。

バックエンドのみをブラウザでデバッグ:

```powershell
python app.py
```

バックエンドは既定で `127.0.0.1:55234` のみをリッスンします。

## EXE のビルド

`build_onefile.bat` をダブルクリックするか、次を実行します。

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name ThingsWindows_v3.0.4 `
  --add-data "index.html;." `
  --add-data "vue.global.prod.js;." `
  --add-data "tailwindcss.js;." `
  --collect-all webview `
  main_window.py
```

実行ファイルは `dist\ThingsWindows_v3.0.4.exe` に生成されます。`build/`、
`dist/`、ログ、実行時データは Git にコミットされません。

## テスト

```powershell
python -m py_compile app.py things_cloud.py sync_engine.py main_window.py
python -m pytest -q
```

テストはメモリ上のシミュレートされた Things Cloud を使用し、実際の
アカウントにはアクセスしません。作成、編集、プロジェクト／エリア移動、
予定、期限、タグ、完了／キャンセル、ゴミ箱、オフラインキューの再起動、
リモート変更のマージ、同一フィールドの競合、競合の再試行／破棄、API
静的アセットを対象としています。

## 同期動作と制限

- 書き込みはディスク上の outbox に入り、Cloud 成功後にのみ削除されます。
- コミットには `ancestor-index` を使用し、HTTP 409 の場合は競合判定前に
  リモート変更を取得します。
- ローカルとリモートが同じフィールドを同時編集した場合、暗黙に上書き
  しません。画面でローカル再試行またはローカル変更の破棄を選択できます。
- 異なるフィールドの同時編集は、可能な範囲でマージされます。
- history 破損を避けるため、現在は繰り返しタスクの変更を拒否します。
- 完全削除は未実装です。復元可能なゴミ箱のみ対応します。
- 添付ファイル本体は Things Cloud にアップロードされず、メモ内の添付
  マーカーのみが同期されます。
- メモは CRC32 チェックサム付きの Things プレーンテキストオブジェクトとして
  書き込まれます。

実装の詳細は[中国語のアーキテクチャ／プロトコル文書](docs/ARCHITECTURE.md)
を参照してください。

## データの保存場所

アプリケーションデータは `%APPDATA%\ThingsWindows\` に保存されます。
各 Cloud history のキャッシュは
`%APPDATA%\ThingsWindows\accounts\<history-hash>\` に保存されます。

- `history_items.json`: ローカル Cloud 状態キャッシュ。
- `known_head.json`: 最後に確認したリモート head。
- `outbox.json`: 送信待ちの書き込み操作。
- `conflicts.json`: 永続化された競合記録。
- `sync_state.json`: 最新の同期状態。
- `credentials.dat`: アプリケーションディレクトリ内の DPAPI 暗号化認証情報。
- `pins.json`: アプリケーションディレクトリ内のピン留め項目。
- `attachments\`: アプリケーションディレクトリ内のローカル添付ファイル。

ログは EXE またはソースディレクトリの `things_debug.log` に書き込まれます。
このファイルは Git の対象外です。

## 参考資料とライセンス

同期プロトコルの参考資料:

- [arthursoares/things-cloud-sdk](https://github.com/arthursoares/things-cloud-sdk)
- [evanpurkhiser/things3-cloud](https://github.com/evanpurkhiser/things3-cloud)

本プロジェクトはコミュニティソースの MIT ライセンスと著作権表示を保持して
います。[LICENSE](LICENSE) を参照してください。同梱の Vue と Tailwind
ブラウザビルドも、それぞれの MIT ライセンス表示を保持しています。
