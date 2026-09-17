# 個人用ローカルLLM Web環境 — 導入・運用パッケージ

**バージョン 0.1.0 / 2026-09-16 / Mac mini向けの初期実装**

OllamaとOpen WebUIをmacOSで直接動かし、既存Tailscale経由で使うためのソース一式です。新しいチャットUIは作らず、Open WebUIの標準画面を利用します。元のOpenClaw・443番経路・Docker Sandboxは変更対象にしません。

**このZIPを作成した時点ではMac miniへ導入していません。** 設定・安全チェック・運用処理は実装済みですが、ここで実行したのはLinux上の自動テストです。Open WebUI本体、Ollama本体、モデル、Python依存物はZIPに含めず、利用者がMac miniで導入コマンドを実行した際に取得します。実機試験が済むまでは初期実装として扱ってください。

## 構成

```text
MacBook / Pixel / iPad のブラウザ
    │ 既存Tailscale / HTTPS 9443
    ▼
Mac mini: Tailscale Serve
    ├─ 443 → 既存OpenClaw :18789（変更しない）
    └─ 9443 → Open WebUI 127.0.0.1:3000
                  ├─ 専用DATA_DIR / SQLite / ローカルChroma
                  └─ Ollama 127.0.0.1:11434（macOSネイティブ）
```

両アプリの実行・データ保存はMac mini側です。MacBookにPythonやOllamaを導入する作業ではありません。設計の詳細は`specs/`の承認済み3文書を参照してください。

## 最初に行うこと

ZIPを**Mac mini**で展開し、そのフォルダをターミナルで開いて以下を実行します。

```bash
bash ./llmweb preflight
```

これは読み取り専用です。MacのOS・メモリ・空き容量・ポート、必要コマンド、Tailscaleの状態を確認します。ホスト名などの私的な情報が表示されるため、結果を公開投稿しないでください。

ターミナルで展開先へ移動する操作が難しい場合は、`setup.command`をFinderから開くと番号メニューが表示されます。メニューの最初は**0：現状確認**です。開けない場合はターミナルで`bash `と入力して`setup.command`をドラッグし、Enterを押します。OSの保護機能を無効にする手順は不要です。

## 事前準備

- Mac mini側で既存Tailscaleに接続し、OpenClawが従来どおり使えることを確認する。
- Ollamaの公式macOS配布物、uv、起動用Python 3.9以上が利用可能であること。本パッケージがOpen WebUI用Python 3.11を別途用意する。
- Ollamaが既に動いている場合は、何に使われているかを先に確認する。**本ツールは既存Ollamaを勝手に終了しない。** 今回の専用LaunchAgentへ起動元を統一する際は、本人が既存アプリの終了・ログイン項目等を確認する。
- 3000番と9443番が空いていること。競合する場合は既存アプリを終了するのではなく、新規側に別ポートを指定する。
- インストールにはパッケージ・トークナイザー・モデルの取得通信とディスク容量が必要。必要容量は依存物・モデルで変わるため、実機で確認する。

公式の準備先は[出典一覧](docs/SOURCES.md)。システム全体のPythonやHomebrewサービスは自動変更しません。Xcode等のライセンスへの同意も代行しません。`sudo`で実行しないでください。

## 導入の流れ

各工程でエラーが出たら、解消するまで次へ進めません。`setup.command`の番号メニューでも同じ工程を実行できます。

### 1. 専用環境を導入

```bash
bash ./llmweb install
```

Open WebUIは**0.11.3を初期検証候補として固定**しています。PyPIの配布情報は確認しましたが、この環境では本体を取得・起動していません。実際のmacOSで依存解決を行い、ハッシュ付きロックを生成します。インストールされた版のソースから設定キーを確認できない場合は、推測で進めず停止します。

初期の試験用モデル指定は`qwen3:4b`と`bge-m3:latest`です。この工程ではモデルを取得しません。最終的な採用モデルや速度の保証ではなく、実機検証の出発点です。モデルタグの`latest`は上流の識別タグであり、毎回自動更新する設定ではありません。取得時にダイジェストを記録します。

必要な場合だけ、初回に明示します。

```bash
bash ./llmweb install --web-port 3002 --https-port 9443 \
  --chat-model qwen3:4b --embed-model bge-m3:latest
```

導入済みの状態で`install`を繰り返しても上書き更新はしません。別版への更新、既存DATA_DIRの移植、モデル保存先の変更は自動化対象外です。

### 2. Ollamaを起動し、試験用モデルを取得

```bash
bash ./llmweb start ollama
bash ./llmweb pull-models
bash ./llmweb smoke
```

モデル取得は確認入力後に行います。`smoke`はOllamaの直接APIへ架空の質問を送り、応答と埋め込みの数値を確認します。**Open WebUIのチャット・文書検索・GPU性能の合格試験ではありません。**

### 3. Mac mini内だけでWebUIを起動し、管理者を作る

```bash
bash ./llmweb start webui
```

Mac miniのブラウザで`http://127.0.0.1:3000`を開き、**自分用の管理者1名だけ**を作成してください。MacBookの`127.0.0.1`ではありません。初回はloopbackのHTTP、遠隔用のServe経路はまだ追加しません。

初期セットアップ中も`WEBUI_URL`の初期値だけは最終HTTPS URLを使います。ブラウザからの実アクセス、CORS、Cookieは初期設定用HTTPに揃えています。後でDBへ保存されたURLと食い違わないための実装上の選択です。SSOは使いません。

### 4. 安全設定を反映し、保存設定も確認

```bash
bash ./llmweb secure
```

作成した管理者のメールとパスワードを入力します。パスワードは画面に表示せず、ファイル・コマンド引数に保存しません。管理APIのトークンもメモリ内だけで使います。

この工程では公式管理APIで設定を取り出し、ローカル接続・登録停止・外部機能停止などを反映して読み戻します。Open WebUIを再起動し、HTTPS用Cookie/CORSへ切り替えます。DBを直接書き換えたり、DATA_DIRを削除したりはしません。

HTTPS運用に移った後、ローカルHTTP画面でログインし続けることは想定しません。次の工程で正規HTTPS経路を追加します。ここで止まった場合はログを確認し、安易にCookieを非Secureへ戻さないでください。

### 5. 専用HTTPS経路を追加

```bash
bash ./llmweb publish
bash ./llmweb doctor
```

管理者認証と実際の保存設定を再確認した後、専用ポートを追加します。追加前後のServe設定を比較し、**既存443番を含む他経路が変化していないこと**を確認します。Tailnetのアクセスルールは自動変更しません。誰がこのポートへ到達できるかはTailscale管理画面で本人が確認してください。

成功時に実機で確認したFQDNによる利用URLが表示されます。MacBookのTailscaleを接続し、そのURLをブラウザで開きます。OpenClawのGatewayトークンは流用しません。

### 6. 受け入れ試験とバックアップ

[受け入れ試験](docs/ACCEPTANCE.md)を行い、MacBookの別回線アクセス、履歴、TXT/MD/文字PDF、OpenClaw共存、再起動、復元を記録します。

```bash
bash ./llmweb backup
```

バックアップ先が表示されます。秘密鍵・会話・添付文書が含まれるので、公開やチャット添付はしないでください。同じMac内のバックアップだけでは本体故障に備えられません。

## コマンド一覧

| コマンド | 動作 |
|---|---|
| `preflight` | 読み取り専用の前提確認 |
| `install` | 専用Python環境、固定版、設定、起動ラッパーを配置 |
| `start [ollama\|webui\|all]` | 専用LaunchAgentの起動 |
| `stop [ollama\|webui\|all]` | 専用ジョブをそのログインセッションで停止 |
| `pull-models` | 指定モデルを明示的に取得 |
| `smoke` | ローカルOllama APIの小さな確認 |
| `secure` | 管理者認証、永続設定の反映・読戻し、HTTPS設定 |
| `publish` / `unpublish` | 記録と一致する新規Serve経路だけを追加・削除 |
| `doctor` | 専用PID、loopback、認証、経路の部分診断 |
| `backup` | 停止中の全DATA_DIR＋設定の整合したコピー |
| `restore-test <バックアップフォルダ>` | 本番を上書きしない隔離コピーで復元を確認 |

## 日常利用と注意

macOSユーザーのログイン後、LaunchAgentがサービスを起動します。FileVault解除、OSログイン、スリープ復帰まで無人で保証する構成ではありません。`start webui`を実行してから初めてWebUIのLaunchAgentが配置されます。初期設定を中断したまま放置せず、未完了時は`stop all`で停止してください。

UIの日本語表示や会話の好みは標準画面で調整します。モデル切替の追加試験は2つ目のローカルチャットモデルを明示的に取得して行ってください。初期パッケージはチャット1種と埋め込み1種を指定するため、AT-06は自動合格にはなりません。

ファイルはTXT/MD/文字PDF、10MiB/ファイルを初期値とします。`RAG_FILE_MAX_COUNT=5`は上流で一度の添付数を制限する設定です。**1会話5ファイルは別途運用ルール**で、累積数の独自制御は作っていません。スキャンPDF・OCR・図表理解は対象外です。

Open WebUIのOFFLINE_MODEはファイアウォールではなく、専用venvはSandboxでもありません。実データを投入する前に[セキュリティと運用](docs/OPERATIONS.md)を読んでください。顧客・患者の実データを使った業務利用、販売・社内多人数配布は今回の完成範囲外です。

## ソースと検証

- `core.py`：設定、経路/権限/DB安全確認、スナップショット、薄い起動処理。
- `llmweb.py`：macOS導入・保守CLI。独自HTTPサーバーは持ちません。
- `tests/`：標準ライブラリだけで動くユニット・模擬HTTP・模擬制御フロー試験。
- `specs/`：承認済みの元仕様3ファイル。
- `samples/`：架空文書と質問・正解例。

```bash
python3 -m unittest discover -s tests -v
```

実行結果・実機で未検証の点は[実装・試験状況](docs/IMPLEMENTATION_STATUS.md)に分けて記載しています。このパッケージは、既存サービスを絶対に壊さないという保証や、第三者によるセキュリティ監査の完了を表すものではありません。
