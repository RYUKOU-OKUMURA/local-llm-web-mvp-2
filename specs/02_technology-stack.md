---
title: "個人用ローカルLLM Web環境 — 技術スタック"
project_id: "local-llm-web"
version: "1.0"
created: "2026-09-16"
as_of: "2026-09-16"
owner: "BOSS"
status: "採用構成の仕様化／実機導入・受け入れ試験は未実施"
---

# 個人用ローカルLLM Web環境 — 技術スタック

## 1. 採用構成

**Mac mini上でOllamaとOpen WebUIをどちらもネイティブ実行し、WebUIだけを既存Tailscale Serve経由で利用する。**

本書は採用技術、設定値、パッケージ管理、モデル管理の基準を定める。完成条件は[要件定義](01_requirements.md)、通信経路と運用順序は[アーキテクチャ](03_architecture.md)に従う。

新規のNext.jsアプリ、独自API、Docker Compose、外部DBは作らない。既存のOpenClaw用Docker Sandboxは、そのまま残す。

## 2. 技術一覧

| 層 | 採用技術 | 役割 | 今回の導入方針 |
|---|---|---|---|
| ハードウェア | 既存Mac mini | 推論とWebUIの共通ホスト | 実際のチップ・メモリは事前確認 |
| OS | Mac miniのmacOS | ネイティブ実行基盤 | バージョンを推測せず記録。OS更新は別作業 |
| 推論 | Ollama macOS版 | チャットと埋め込みモデルの実行 | 公式配布物を使用。サーバーは1プロセス |
| Webアプリ | Open WebUI | チャット、履歴、モデル選択、文書添付 | PyPI配布の安定版を専用環境へ導入 |
| Python | Python 3.11系 | Open WebUIと依存ライブラリの実行 | 専用環境の実際のパッチ版を固定・記録 |
| Python管理 | uv + venv | ランタイムと依存関係の分離 | プロジェクト専用venv。システムPythonを変更しない |
| アプリ内部 | Open WebUI同梱のフロントエンド・バックエンド | 標準製品の動作 | ソース改変・フロントエンドビルドを行わない |
| 履歴DB | Open WebUI標準SQLite | 会話・ユーザー・設定等 | 外部DBサーバーを追加しない |
| 文書索引 | ローカルChroma | 文書検索のインデックス | 単一worker・単一インスタンス |
| ファイル保存 | Mac miniのローカルファイルシステム | 添付文書・アプリデータ | DATA_DIRを絶対パスで固定 |
| ネットワーク | 既存Tailscale | 端末とMac mini間の到達経路 | 既存Tailnetと既存アカウントを流用 |
| HTTPS入口 | Tailscale Serve | Tailnet内HTTPSとリバースプロキシ | 新規9443番からWebUIへ。443番は維持 |
| 認証 | Open WebUI標準アカウント | WebUI利用者認証 | 本人用1アカウント。SSOは使わない |
| 起動管理 | macOS launchd / LaunchAgent | ログイン後起動・異常終了時の再起動 | 本プロジェクト専用の2ジョブ |
| ログ・保守 | ローカルログ、構成記録、停止中バックアップ | 切り分けと復元 | 新規監視サーバー・バックアップ製品は導入しない |

公式資料上、OllamaはmacOS 14以降に対応し、Apple MシリーズではCPU/GPU、x86ではCPUを利用する。実機のチップ種別を確認してからGPU利用を判断する。[OL-01]

Open WebUIのPython環境は公式手段である。初期ベースラインは、公式が重点的にテストしているPython 3.11系とする。[OW-01]

## 3. バージョンの固定方針

### 3.1 「未確認の最新版」を採用版として書かない

本仕様作成時点では実機導入をしていないため、OllamaとOpen WebUIの採用バージョン番号は未確定とする。導入担当者は公式配布物の安定版、macOS/Pythonの互換性、既知の不具合を確認し、起動試験を通過した組み合わせを記録する。

`latest`指定による毎回の再取得、無条件の自動更新、`main`ブランチからの直接インストールは通常運用では行わない。Ollamaアプリの更新通知や自動ダウンロードがあっても、適用は保守として扱い、稼働バージョンを再記録する。

### 3.2 導入完了時の構成記録

`records/versions.md`に次を記録する。これは実装時に作る運用ファイルであり、この仕様ZIPに実測値として同梱したものではない。

| 項目 | 記録内容 |
|---|---|
| macOS | OSバージョン、ビルド番号、CPUアーキテクチャ |
| ハード | Mac miniのチップ、搭載メモリ、検証時の空き容量 |
| Ollama | バージョン、実行バイナリの絶対パス、配布元、起動元 |
| Open WebUI | 正確なパッケージバージョン |
| Python / uv | 正確なバージョン、実行ファイルの絶対パス |
| 依存関係 | `requirements.in`、解決済み`requirements.lock`、チェック結果 |
| Tailscale | 稼働バージョン、Serveの変更前後の状態、正規URL |
| チャットモデル | 正式タグ、ダイジェスト、量子化、サイズ、採用文脈長 |
| 埋め込みモデル | 正式タグ、ダイジェスト、ベクトル次元、評価した言語 |
| 保存先 | DATA_DIR、モデル保存先、バックアップ先 |
| 検証 | 要件定義のAT番号、日時、結果、残余リスク |

モデルタグは更新され得るため、同じタグ名だけで同一モデルと判断しない。Ollamaのモデル一覧APIにはダイジェスト等の情報がある。[OL-03]

## 4. パス・ポート・サービス名の共通契約

以下を3文書共通の初期ベースラインとする。

### 4.1 パス

| 名前 | 値・扱い |
|---|---|
| APP_ROOT | `$HOME/Library/Application Support/local-llm-web` |
| DATA_DIR | `$APP_ROOT/data` |
| Python環境 | `$APP_ROOT/venv` |
| 設定 | `$APP_ROOT/config` |
| 起動スクリプト | `$APP_ROOT/scripts` |
| ログ | `$APP_ROOT/logs` |
| 証跡・構成記録 | `$APP_ROOT/records` |
| ローカルバックアップ | `$APP_ROOT/backups`。DATA_DIRの外に置く |
| Ollamaの標準モデル置場 | `$HOME/.ollama/models`。既存の変更設定があれば先に調査 |
| LaunchAgent定義 | `$HOME/Library/LaunchAgents` |

`$APP_ROOT`は説明用の変数であり、Open WebUIの設定項目ではない。plist内では`~`やシェル変数の自動展開に依存せず、実際の絶対パスを使う。パスに空白が含まれるため、シェルコマンドでは必ず引用する。

DATA_DIRをOpenClawのworkspace、クラウド同期フォルダ、仮想環境内部、uvの一時キャッシュへ置かない。venvを作り直しても会話データを残せる配置とする。

### 4.2 ポート

| 用途 | 待受・経路 | 変更方針 |
|---|---|---|
| Ollama | `127.0.0.1:11434` | LAN・Tailnetへ直接公開しない |
| Open WebUI | `127.0.0.1:3001` | Python版の既定値ではなく明示指定 |
| WebUI入口 | `https://<MAC_MINI_FQDN>:9443` | Serveへ新規追加。空き確認必須 |
| 既存OpenClaw | `127.0.0.1:18789` | 変更しない |
| 既存OpenClaw入口 | 既存HTTPS 443番 | 変更しない |

Open WebUI CLIの参照実装では、既定hostは`0.0.0.0`、portは8080である。したがって、hostとportを明示することを必須とする。採用版でも`serve --help`を確認する。[OW-06]

### 4.3 起動ジョブ

| ラベル | 実行対象 |
|---|---|
| `local.llmweb.ollama` | 確認済みOllamaバイナリの`serve` |
| `local.llmweb.webui` | 専用venv内の`open-webui serve --host 127.0.0.1 --port 3001` |

Ollamaは公式macOS配布物から利用可能なバイナリを導入し、通常運用のサーバー起動は専用LaunchAgentに一本化する。Ollama.appのログイン起動と重ねない。既にOllamaが別用途で使われている場合は、その利用を確認するまで起動元を変更しない。

## 5. Python環境の作り方

### 5.1 採用方法

uvでPython 3.11の専用venvを用意し、Open WebUIの正確なバージョンを指定して依存関係を解決する。通常起動はvenv内の実行ファイルから行い、`uvx ...@latest`を常用しない。

uvはvenv作成、依存解決、ロックファイルへの出力、環境への同期を提供している。[UV-01][UV-02]

### 5.2 導入担当者向けコマンド例

以下は**事前調査後、APP_ROOTに既存環境がないことを確認してから使う例**である。採用版を未確認のまま実行しない。既存venvがある場合は上書きせず、内容を調査する。

```bash
# OPENWEBUI_VERSIONには、確認済みの正確な安定版番号を設定する。
: "${OPENWEBUI_VERSION:?Open WebUIの採用版を先に確定してください}"
APP_ROOT="$HOME/Library/Application Support/local-llm-web"

umask 077
mkdir -p "$APP_ROOT/config" "$APP_ROOT/data" "$APP_ROOT/scripts" \
  "$APP_ROOT/logs" "$APP_ROOT/records" "$APP_ROOT/backups"

uv python install 3.11
uv venv --python 3.11 "$APP_ROOT/venv"
printf 'open-webui==%s\n' "$OPENWEBUI_VERSION" \
  > "$APP_ROOT/config/requirements.in"

uv pip compile --python "$APP_ROOT/venv/bin/python" \
  "$APP_ROOT/config/requirements.in" \
  --output-file "$APP_ROOT/config/requirements.lock"

uv pip sync --python "$APP_ROOT/venv/bin/python" \
  "$APP_ROOT/config/requirements.lock"
uv pip check --python "$APP_ROOT/venv/bin/python"
"$APP_ROOT/venv/bin/open-webui" serve --help
```

依存解決結果は当該macOS・Python環境のものとして保管する。ロックファイルがあっても、別OSや将来の異なるPythonへ無条件に移植できるとは扱わない。

インストール時にXcode/Command Line Tools関連のエラーが出ても、OSや既存環境を無条件に変更しない。原因を記録し、ユーザー自身が確認すべきライセンス同意を自動で代行しない。

## 6. 設定管理の原則

### 6.1 設定の持ち主を分ける

| 種類 | 管理場所 | 運用ルール |
|---|---|---|
| プロセス条件 | 起動スクリプト、LaunchAgent | host、port、DATA_DIR、venvを固定 |
| 起動時の秘密情報 | 権限600の`config/webui.env` | `WEBUI_SECRET_KEY`をローカル生成・固定 |
| Ollama設定 | `config/ollama.env` | 専用プロセスへ渡す。ユーザー全体へ無差別に設定しない |
| WebUIの永続設定 | WebUI管理画面・アプリDB | 接続先・登録可否等の実際の値を記録・確認 |
| ユーザーの好み | WebUI標準設定 | 日本語、モデル等。安全設定と混同しない |
| バージョン・変更履歴 | `config`と`records` | 秘密情報を除いて記録 |

Open WebUIは一部設定をDBに永続化し、以後は環境変数より保存値を優先する。本構成では`ENABLE_PERSISTENT_CONFIG=true`を維持し、管理画面での設定を保存できるようにする。したがって、下記環境変数だけを変更して設定完了と判断しない。[OW-03]

特に新規登録、Ollama接続先、クラウド接続、Web検索、埋め込み設定は、**初期値・管理画面の実値・再起動後の実値**を照合する。設定が食い違った場合もDATA_DIRを削除して解決しない。

### 6.2 通常運用時のOpen WebUI設定例

次は初期セットアップ完了後のベースライン。`<...>`は導入時に解決するプレースホルダーであり、そのまま使わない。環境ファイルは本プロジェクトが起動時に読み込むものであり、Open WebUIが任意の`.env`を自動で読むことを前提にしない。

```bash
# config/webui.env — 通常運用用。権限600、Git登録禁止。
DATA_DIR='/Users/<MACOS_USER>/Library/Application Support/local-llm-web/data'
WEBUI_URL='https://<MAC_MINI_FQDN>:9443'
CORS_ALLOW_ORIGIN='https://<MAC_MINI_FQDN>:9443'
WEBUI_SECRET_KEY='<LOCALLY_GENERATED_SECRET>'
WEBUI_AUTH=true
ENABLE_LOGIN_FORM=true
ENABLE_SIGNUP=false
ENABLE_PERSISTENT_CONFIG=true
WEBUI_SESSION_COOKIE_SECURE=true
WEBUI_SESSION_COOKIE_SAME_SITE=lax

ENABLE_OLLAMA_API=true
OLLAMA_BASE_URLS='http://127.0.0.1:11434'
ENABLE_OPENAI_API=false
ENABLE_DIRECT_CONNECTIONS=false
ENABLE_WEB_SEARCH=false
ENABLE_CODE_EXECUTION=false
ENABLE_CODE_INTERPRETER=false
ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=false
SAFE_MODE=true

RAG_EMBEDDING_ENGINE=ollama
RAG_EMBEDDING_MODEL='<EMBED_MODEL_TAG>'
RAG_OLLAMA_BASE_URL='http://127.0.0.1:11434'
CONTENT_EXTRACTION_ENGINE=''
VECTOR_DB=chroma
UVICORN_WORKERS=1
OFFLINE_MODE=true
HF_HUB_OFFLINE=1
```

この例は設計用の設定一覧であり、採用するOpen WebUI版で名前・動作・永続化区分を照合してから使う。`WEBUI_SESSION_COOKIE_SECURE`は認証Cookieの設定にもフォールバックし得るため、認証Cookie側に個別の保存値・設定値がある場合もSecure属性を確認する。[OW-03]

**初回のみの違い：** ローカルのHTTP画面で管理者を作る間は`ENABLE_SIGNUP=true`、`WEBUI_SESSION_COOKIE_SECURE=false`、URL/Originはローカル確認用にする。管理者作成後に登録を閉じ、通常運用のHTTPS値へ切り替え、再起動とログイン試験を行う。初期管理者未作成の状態で9443番を公開しない。

OFFLINE_MODEを有効化する前に、ローカルモデルと文書処理に必要な依存物を準備する。クリーン環境にこの例をそのまま投入して起動することを想定しない。

### 6.3 補足設定と禁止事項

| 項目 | 方針 |
|---|---|
| Ollama接続のURL | ネイティブ接続を使い、`/v1`や`/api`を末尾に付けない |
| 接続先の複数形 | `OLLAMA_BASE_URLS`の設定・保存値を点検。古い接続先を残さない |
| 外部APIキー | 登録しない。既存キーを他アプリからコピーしない |
| Functions / Tools / Pipelines | 追加・インポートしない。Safe Modeだけで全拡張を制御できるとは考えない |
| コード実行 | 実行・インタープリターの両方を無効化 |
| 新機能 | 自動化、サブエージェント、メモリ、画像・音声、外部接続は未使用。採用版で有効なものを点検 |
| Trusted headers / SSO | 未設定。TailscaleヘッダーだけでWebUIへ自動ログインさせない |
| CORS | 本番は正規HTTPS Originだけ。`*`での回避をしない |
| DATABASE_URL | 初期構成では設定せず、Open WebUI標準SQLiteを使う |
| CHROMA_HTTP_HOST | 設定しない。外部Chromaサーバーを立てない |
| TLS検証 | ブラウザや接続で無効にしない |

Open WebUI公式は、不要なコード実行の停止、Functions/Toolsへの注意、追加依存の自動インストール制限などを説明している。[OW-05]

## 7. Ollama設定

### 7.1 通常運用の初期値

```bash
# config/ollama.env — 専用LaunchAgentが起動するプロセスへ渡す。
OLLAMA_HOST='127.0.0.1:11434'
OLLAMA_NO_CLOUD=1
OLLAMA_NUM_PARALLEL=1
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_CONTEXT_LENGTH=4096
```

loopback待受とクラウド機能停止は必須。並列数1、同時ロード1、文脈長4096は**初期の資源制御用設計値**であり、性能保証や全モデル共通の最適値ではない。モデルの対応範囲と実機測定を踏まえて調整する。[OL-02]

`OLLAMA_NO_CLOUD=1`の反映後は、サーバーログとモデル一覧を確認する。Ollamaにクラウドモデル・Web検索を使わせない。サーバー設定を変えた場合は対象プロセスを再起動する。[OL-02]

### 7.2 モデル切替と文書検索の負荷

同時ロード数を1にすると、文書検索用の埋め込みモデルとチャットモデルを順番に入れ替える場面がある。その読み込み時間は初期構成で許容する。速くするために同時ロード数を増やすのは、OpenClawへの影響とメモリ余裕を測定してからにする。

Open WebUIのモデル別`num_ctx`が指定されていると、Ollamaサーバーの文脈長設定よりリクエスト値が優先される場合がある。両者を照合し、意図せず大きな文脈長を要求しない。[OW-03]

チャットモデルは日本語の実用性、文脈長、速度、メモリ使用量で選ぶ。埋め込みモデルは「日本語文書の関連箇所を取得できるか」を別途評価する。名称やパラメータ数だけで決めない。

### 7.3 モデルを登録するときの記録

| 属性 | 記録例の形式 |
|---|---|
| 用途 | チャット用 / 埋め込み用 |
| タグ | `<CHAT_MODEL_TAG>`または`<EMBED_MODEL_TAG>`を実際の値へ置換 |
| 識別 | モデルダイジェスト |
| 推論設定 | 文脈長、量子化、出力上限 |
| 動作 | コールド/ウォーム応答、メモリ、GPU利用状況 |
| 制約 | 対応言語、非対応機能、モデル配布条件 |

最初から複数の大規模モデルをまとめて取得しない。導入試験用の小さなモデルから開始し、モデル切替試験用の2つ目は必要になった時点で取得する。取得にはネット接続が必要であり、オフラインチャット機能とは分ける。

## 8. 文書処理の技術範囲

採用する処理経路は、**標準ローカル抽出 → ローカル埋め込み → ローカル索引 → 検索結果を含めたローカル推論**とする。

初期対象はTXT・MD・テキストPDF。外部ドキュメント解析サービス、OCRサーバー、画像認識モデル、表計算専用処理は導入しない。標準ローダーで扱えない文書を無理に対応させない。

Open WebUIはOllamaを埋め込みエンジンとして選択でき、Ollamaには埋め込みAPIがある。検索インデックスはアプリの内部機能として使い、別途RAGアプリを開発しない。[OW-03][OL-04]

初期値は以下とする。

| 項目 | 方針 |
|---|---|
| ファイル上限 | 10MiB/ファイル、5ファイル/会話。製品上限ではなく運用上限 |
| 抽出 | 標準ローカルローダー |
| 埋め込み | Ollamaの選定済みローカルモデル |
| ベクトル保存 | Chromaのローカル永続化 |
| ハイブリッド検索 | 初期は使わない |
| リランキング | 初期は使わない |
| チャンク等 | まず標準値。日本語文書試験の結果が悪い場合だけ調整 |
| インデックス再作成 | 埋め込みモデル変更時は必要性を確認し、互換性を仮定しない |

標準Chromaはローカル永続化のため、単一worker・単一インスタンスを維持する。workersを増やして性能問題を解消しようとしない。[OW-03]

## 9. ネットワーク技術と設定例

### 9.1 到達経路

```text
ブラウザ
  → https://<MAC_MINI_FQDN>:9443
  → 既存Tailscale Serve
  → http://127.0.0.1:3001
  → http://127.0.0.1:11434
```

ブラウザにOllamaの接続先やAPIキーを持たせない。Open WebUIのバックエンドからOllamaへアクセスする。MacBookの`localhost`はMacBook自身なので、モデル接続先として入力しない。[OW-02]

### 9.2 既存Serveを確認した後の追加例

```bash
# 読み取り確認。出力には内部ホスト名等が含まれるため公開しない。
tailscale serve status
tailscale serve status --json

# WebUI起動・管理者作成・登録停止・9443未使用確認後の追加例。
tailscale serve --bg --https=9443 http://127.0.0.1:3001

# 追加後に既存443番が変わっていないことを確認。
tailscale serve status
```

ServeはTailnet内に共有する機能であり、アクセスルールも適用される。Funnelでの公開は行わない。[TS-01][TS-02]

`--bg`によるServeの継続と、WebUI/Ollamaの起動管理は別である。Serveが残っていても接続先アプリが停止していれば利用できない。[TS-01]

既存OpenClawが起動・停止時にServe設定を自動管理しているかは未確認。アプリ再起動後も両経路が残ることを受け入れ試験AT-16で確認する。

## 10. launchdの実装条件

### 10.1 起動元の一本化

Ollama・Open WebUIともユーザー単位のLaunchAgentを採用する。Ollama.app、Homebrewサービス、別のLaunchAgent、手動`ollama serve`が同時に稼働していないことを確認する。

LaunchAgentはユーザーセッションで動かす設計とし、root権限のLaunchDaemonや自動ログインを初期構成へ追加しない。Appleのlaunchd資料に従ってジョブを定義し、実機でログイン後の復帰を確認する。[OS-01]

### 10.2 各ジョブに持たせるもの

| 項目 | 条件 |
|---|---|
| ProgramArguments | 固定の起動スクリプトの絶対パス |
| WorkingDirectory | APP_ROOTの絶対パス |
| RunAtLoad | 有効 |
| KeepAlive | 異常終了時の再起動を有効化し、再試行間隔を確保 |
| ThrottleInterval | 初期案10秒。連続失敗時は手動でジョブを停止し原因調査 |
| stdout / stderr | プロジェクトのlogs内へ分離 |
| 環境 | スクリプトから専用envファイルを読み込む |
| 権限 | 実行ユーザーはBOSSの運用用アカウント。root実行しない |

起動スクリプトは外部サービスや独自APIではなく、環境変数を読み込んで既存実行ファイルへ`exec`する薄いラッパーに留める。シェルの`source`対象は所有者のみが書ける設定ファイルに限定する。`set -x`を使って秘密情報をログへ出さない。

起動順の競合を避けるため、WebUI側のラッパーはOllamaの一覧APIを有限回リトライしてから起動する。60秒程度を初期の待機上限とし、無限ループで待たない。上限後はログを出して終了し、launchdの再試行に任せる。推論モデルをロード済みにすることまでは起動条件にしない。

Ollamaの読み取り確認には`/api/tags`、ロード中モデルの確認には`/api/ps`を利用できる。[OL-03][OL-06]

## 11. 保存・バックアップ・更新

### 11.1 保存対象

Open WebUIのDATA_DIR全体、envファイル、起動スクリプト、LaunchAgent定義、依存ロック、バージョン記録を復元対象に含める。`WEBUI_SECRET_KEY`を変更せず復元できるよう安全に保管する。

SQLiteだけをコピーして、添付ファイルや文書索引を取りこぼさない。Open WebUIのフォルダ構造は採用版により変わり得るため、内部ファイルを手作業で選別するよりDATA_DIR全体を保存する。[OW-05][OW-07]

### 11.2 更新の最小手順

1. 採用版から更新候補への変更内容を確認する。
2. Open WebUIのジョブを停止し、勝手に再起動しない状態で整合したバックアップを取る。
3. 依存ロックと採用バージョンを更新し、必要なら新しいvenvで確認する。
4. 単一workerで起動し、DB移行とログを確認する。
5. 認証、履歴、ローカル接続、文書添付、OpenClaw共存を再試験する。
6. 失敗時は旧アプリ版と対応するデータのバックアップを一組で戻す。

DB移行後にアプリだけをダウングレードしない。Ollama更新も別工程で行い、同時に複数コンポーネントを変更して原因を不明にしない。[OW-07]

### 11.3 ログとディスク

ログはまず通常の情報レベルを用い、プロンプト全文・添付本文・Cookie・秘密鍵の詳細ログを常時保存しない。ローテーションは既存のOS/保守手段で行い、独自の監視システムは作らない。

初期のログ保管は7日を目安とし、容量増大時には先に原因を確認する。データやモデルを空き容量確保のために無断削除しない。同じディスクへのバックアップはハード故障対策にはならない。

## 12. 不採用の技術

| 技術・構成 | 不採用理由 |
|---|---|
| Ollama / Open WebUIのDocker化 | 今回は両方macOS直接実行で決定済み |
| LM Studio | ユーザーはOllamaを選択済み |
| Next.js等の自作UI | Open WebUIで要求を満たす。新規開発をしない |
| 独自LLM中継API | Open WebUIがOllamaへ直接接続できる |
| Nginx / Caddy追加 | 既存Tailscale Serveで必要な入口を構成する |
| Redis / PostgreSQL / 外部ベクトルDB | 1人・1台・1workerでは追加しない |
| クラウドLLM / クラウド埋め込み | 本プロジェクトのローカル処理要件に反する |
| SSO / Trusted headers | 個人用標準ログインで足りる |
| モデル自動更新・最新版追従 | 安定性と再現性を優先 |
| OpenClawとの共通DB・共通workspace | データと障害の影響を切り離す |

## 13. 出典・根拠

Web資料は2026年9月16日に確認。公式の現在文書と採用版の仕様が食い違う場合は、採用版の実装・ヘルプ・リリース内容を確認して記録する。URLの`main`は参照先であってインストール対象ではない。

- **[OL-01]** Ollama / macOS：<https://docs.ollama.com/macos>
- **[OL-02]** Ollama / FAQ：<https://docs.ollama.com/faq>
- **[OL-03]** Ollama / List models：<https://docs.ollama.com/api/tags>
- **[OL-04]** Ollama / Generate embeddings：<https://docs.ollama.com/api/embed>
- **[OL-06]** Ollama / List running models：<https://docs.ollama.com/api/ps>
- **[OW-01]** Open WebUI / Python environments：<https://docs.openwebui.com/getting-started/quick-start/install-methods/python-environments/>
- **[OW-02]** Open WebUI / Ollama connection：<https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-ollama/>
- **[OW-03]** Open WebUI / Environment Variable Configuration：<https://docs.openwebui.com/reference/env-configuration/>
- **[OW-05]** Open WebUI / Hardening：<https://docs.openwebui.com/getting-started/advanced-topics/hardening/>
- **[OW-06]** Open WebUI / CLI実装：<https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/__init__.py>
- **[OW-07]** Open WebUI / Updating：<https://docs.openwebui.com/getting-started/updating/>
- **[TS-01]** Tailscale / Serve command：<https://tailscale.com/docs/reference/tailscale-cli/serve>
- **[TS-02]** Tailscale / Serve：<https://tailscale.com/docs/features/tailscale-serve>
- **[UV-01]** uv / Using environments：<https://docs.astral.sh/uv/pip/environments/>
- **[UV-02]** uv / Locking environments：<https://docs.astral.sh/uv/pip/compile/>
- **[OS-01]** Apple / Creating Launch Daemons and Agents：<https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html>
