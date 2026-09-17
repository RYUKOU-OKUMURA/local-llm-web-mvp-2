---
title: "個人用ローカルLLM Web環境 — アーキテクチャ"
project_id: "local-llm-web"
version: "1.0"
created: "2026-09-16"
as_of: "2026-09-16"
owner: "BOSS"
status: "採用構成の仕様化／実機導入・受け入れ試験は未実施"
---

# 個人用ローカルLLM Web環境 — アーキテクチャ

## 1. 設計の要点

**Mac miniを唯一の実行・保存拠点にし、OllamaとOpen WebUIをmacOS上で直接実行する。MacBookなどはTailscale越しのブラウザクライアントとする。**

既存のOpenClawと共有するのは、Mac miniのハードウェア、macOS、Tailscaleという基盤だけである。会話DB、アプリ設定、workspace、認証情報を共通化しない。

本書の構成図は設計図であり、実機で導入済みという意味ではない。機能・試験基準は[要件定義](01_requirements.md)、パラメータの正本は[技術スタック](02_technology-stack.md)に置く。

## 2. 全体配置

```text
利用端末：MacBook / Pixel / iPad
┌─────────────────────────────────────────────┐
│ ブラウザ：Open WebUI                         │
│ Tailscaleクライアント                        │
│ Ollama・Python・ローカルDBは不要              │
└─────────────────────┬───────────────────────┘
                      │ HTTPS
                      │ https://<MAC_MINI_FQDN>:9443
                      │ 既存Tailnet内の許可された接続
                      ▼
Mac mini（既存ホスト）
┌───────────────────────────────────────────────────────┐
│ 既存Tailscale / Tailscale Serve                        │
│                                                       │
│  HTTPS 443 ──────────→ 既存OpenClaw Gateway            │
│                       127.0.0.1:18789                  │
│                       └─ 既存Docker Sandbox           │
│                       ※ここは変更しない               │
│                                                       │
│  HTTPS 9443 ─────────→ Open WebUI                     │
│                       127.0.0.1:3000                  │
│                       専用Python 3.11 venv            │
│                       単一worker / 標準アカウント      │
│                             │                         │
│                             ├─ DATA_DIR               │
│                             │  ├─ 会話・設定等のDB    │
│                             │  ├─ 添付文書            │
│                             │  └─ ローカル文書索引    │
│                             │                         │
│                             └─ HTTP（loopback）       │
│                                  ▼                    │
│                               Ollama                  │
│                               127.0.0.1:11434         │
│                               macOSネイティブ         │
│                               ├─ チャットモデル       │
│                               └─ 埋め込みモデル       │
└───────────────────────────────────────────────────────┘
```

Mac mini自身のブラウザも、通常利用では同じ正規HTTPS URLを使う。初期設定とローカル診断だけ`127.0.0.1:3000`を使う。MacBookに「同じアプリ一式」を導入して同期する構成ではない。

Tailscale ServeはローカルサービスをTailnet内へ共有する機能であり、HTTPSポートを指定できる。既存経路を維持しながら別の入口を追加する設計に用いる。[TS-01][TS-02]

## 3. コンポーネントの責務

| コンポーネント | 担当すること | 担当しないこと |
|---|---|---|
| ブラウザ | UI表示、入力、ログイン、ファイル選択、回答表示 | MacBook上でのLLM推論、DB同期 |
| Tailscale | 端末間の到達経路とネットワーク認証 | Open WebUIのユーザーDB・会話保存 |
| Tailscale Serve | HTTPS入口、WebUIへの転送 | LLM推論、OpenClawとWebUIの認証統合 |
| Open WebUI | アカウント、会話、モデル選択、添付処理、検索、表示用応答 | 独自に開発したUIやエージェント機構 |
| Ollama | ローカルモデルの読み込み、生成、埋め込み | WebUIの会話履歴・ユーザー認証管理 |
| SQLite | Open WebUI標準の構造化データ保存 | MacBookとのファイル同期 |
| ローカルChroma | 文書検索用インデックス | 外部ナレッジの自動収集 |
| LaunchAgent | プロセス起動・再試行・ログ出力先の指定 | OSログイン・FileVault解除・ハード故障対応 |
| バックアップ | 既知の状態へ戻すための保存 | 自動的な冗長化・サービス無停止保証 |

Open WebUIはネイティブのOllama接続を利用する。クライアントからOllamaへ直接接続させず、WebUIバックエンドから呼び出す。[OW-02]

## 4. 通信・データフロー

### 4.1 ログインと画面表示

1. 利用端末が既存Tailnetへ接続する。
2. ブラウザが`https://<MAC_MINI_FQDN>:9443`を開く。
3. Tailscaleのアクセス制御を通ったリクエストをServeが受ける。
4. Serveが`http://127.0.0.1:3000`のOpen WebUIへ転送する。
5. Open WebUIの標準ログインを行う。
6. 認証済みユーザーとして会話一覧とモデル一覧を取得する。

Tailscaleに接続できることと、WebUIにログインできることは同一ではない。信頼ヘッダーを用いた自動認証は初期構成に含めない。

### 4.2 チャット生成

```text
ブラウザ          Open WebUI             Ollama          ローカルモデル
   │ 認証済み入力       │                    │                    │
   ├──────────────────→│                    │                    │
   │                    │ 接続・権限等確認   │                    │
   │                    │ ローカル生成要求   │                    │
   │                    ├──────────────────→│                    │
   │                    │                    │ 必要時モデル読込   │
   │                    │                    ├──────────────────→│
   │                    │                    │← 逐次生成 ─────────┤
   │                    │← ストリーム ──────┤                    │
   │← 逐次表示 ─────────┤                    │                    │
   │                    │ 会話情報を保存     │                    │
```

OllamaのネイティブチャットAPIは`POST /api/chat`である。実際にどのタイミングで会話が保存されるか、ブラウザ向けストリームやWebSocketをどう使うかは採用版のOpen WebUIに従う。独自の通信層は作らない。[OL-05]

画面が表示されるだけで接続成功としない。逐次表示、長めの回答、生成停止、別回線からの利用まで確認する。WebSocketを使用する経路についてもOrigin設定を合わせる。[OW-04]

### 4.3 文書添付と文書検索

```text
利用者が選んだファイル
        │ HTTPS upload
        ▼
Open WebUI / 標準ローカル抽出
        │ テキスト化・分割
        ▼
Ollama / ローカル埋め込みモデル
        │ ベクトル化
        ▼
DATA_DIR / ローカル文書索引
        │
質問 → 質問の埋め込み → 関連部分を検索
        │
        ▼
質問 + 検索結果 → Ollama / チャットモデル → 回答・参照元
```

これは標準RAGの概念フローであり、すべての添付が常に同じ内部処理になる保証ではない。短い文書を全文脈で扱う場合など、採用版・設定による違いは記録する。

Ollamaの埋め込みAPI、Open WebUIのOllama埋め込みエンジンを使い、外部サービスを追加しない。[OL-04][OW-03]

文書中の文章は利用者が提供したデータとして扱う。「設定を変えろ」「外部へ送れ」といった文書内の指示に従うツールを接続しない。ただし、ツールを接続しないことは回答の誤りや不適切な文書解釈を完全に防ぐ保証ではない。

### 4.4 他端末から履歴を開く

MacBook、Mac mini、Pixel、iPadの各ブラウザは同じOpen WebUIへアクセスする。履歴の正本はMac miniにある。別端末では再読み込みしてから最新履歴を利用する。同じ会話の同時編集・複数端末からの同時生成は初期運用で避ける。

## 5. 保存設計

### 5.1 ディレクトリ構成

```text
~/Library/Application Support/local-llm-web/
├─ config/
│  ├─ webui.env                 # ローカル生成秘密鍵を含む。権限600
│  ├─ ollama.env                # Ollamaプロセス用設定
│  ├─ requirements.in           # Open WebUIの固定バージョン
│  └─ requirements.lock         # 実機で解決した依存関係
├─ venv/                        # Python専用環境。データ保存先にしない
├─ scripts/
│  ├─ start-webui.sh            # 実装時に作る最小起動ラッパー
│  └─ start-ollama.sh            # 実装時に作る最小起動ラッパー
├─ data/                        # DATA_DIR。内部構造は製品が管理
│  ├─ webui.db                  # 標準SQLiteの例
│  ├─ 添付ファイル・キャッシュ   # 実際のサブディレクトリを実機で記録
│  └─ 文書索引                  # 実際のサブディレクトリを実機で記録
├─ logs/
│  ├─ webui.stdout.log
│  ├─ webui.stderr.log
│  ├─ ollama.stdout.log
│  └─ ollama.stderr.log
├─ records/
│  ├─ versions.md               # 実際のバージョンとモデル情報
│  ├─ setup-result.md           # AT番号に対応する検証結果
│  └─ serve-before-after/       # 公開しない、権限を絞った状態記録
└─ backups/
   └─ YYYYMMDD-HHMMSS/          # 停止中に取得した復元単位

~/.ollama/
└─ models/                      # 標準モデル保存先。既存設定を先に確認

~/Library/LaunchAgents/
├─ local.llmweb.ollama.plist
└─ local.llmweb.webui.plist
```

図中の起動スクリプトや運用記録は、実装時の成果物仕様である。このZIPは3つの設計文書のみを含み、実行済みスクリプトや実機の設定値を含まない。

DATA_DIRは固定する。Open WebUIのPython/uv導入でデータ保存先を明示することは、公式にも案内されている。[OW-01]

### 5.2 データ種別ごとの扱い

| データ | 保存場所 | バックアップ | 注意事項 |
|---|---|---|---|
| 会話・ユーザー・設定 | DATA_DIR内のアプリDB | 必須 | WebUIがスキーマを管理。直接編集を常用しない |
| 添付・抽出結果・索引 | DATA_DIR | 必須 | SQLiteだけを保存して終わらせない |
| WEBUI_SECRET_KEY | config/webui.env | 必須・厳重保管 | 起動ごとに生成し直さない |
| モデル本体 | Ollamaのモデル保存先 | 原則は再取得可能性を確認 | 独自モデルや取得不能な版は別途保全 |
| モデル識別情報 | records/versions.md | 必須 | タグだけでなくダイジェストを記録 |
| ライブラリ | venv | 再構築用記録を必須にする | ロック・Python版・配布物の再取得可能性が必要 |
| ログ | logs | 調査に必要な範囲 | 不要な本文・認証情報を保存しない |
| ブラウザ状態 | 各利用端末 | サーバー復元の対象外 | 認証状態・キャッシュ・手動DLは端末にも残り得る |

### 5.3 バックアップと削除

バックアップの単位は「ある時点のアプリ版＋DATA_DIR全体＋秘密鍵と設定＋構成記録」とする。バックアップ取得時にはOpen WebUIのジョブを停止し、自動再起動がかからない状態にする。[OW-07]

定期頻度は週1回を初期運用案とし、加えて更新前、導入完了時、重要な設定変更前に取得する。自動化は既存手段で必要な範囲に留める。標準チャットエクスポートを、完全バックアップの代用にしない。

削除した会話や添付が過去バックアップに残ることを明記する。画面からの削除を、記憶媒体上の完全消去や全世代バックアップからの消去と同一視しない。

## 6. セキュリティ境界

### 6.1 ネットワーク境界

外部端末に許す入口はServeの9443番だけとする。Open WebUIの3000番とOllamaの11434番は`127.0.0.1`のみ。`0.0.0.0`、`::`、LAN IP、Tailscale IPで直接待ち受けさせない。

同じTailnetに所属しているだけで本人限定になるわけではない。既存のアクセスルールを確認し、新規ポートを誰が使えるかを記録する。ルール変更が必要でも、既存のOpenClawの権限を消したり広げたりせず、新規用途に限った最小差分にする。[TS-02]

### 6.2 認証境界

Open WebUIのログインを常に有効にする。初期管理者作成後は新規登録を止める。OpenClawのトークン、Tailscaleの認証キー、macOSのログインパスワードを流用しない。

Serveが付加する信頼ヘッダーをWebUIの認証へ結び付けない。WebUI自体の認証を残し、単純な構成にする。[TS-02]

### 6.3 同一ホスト・別ポートの限界

443番と9443番に分けることで経路の衝突を避ける。ただしCookieはポートによる分離を保証しない。同じホスト名のサービス間でCookieが送られ得るため、「別ポートだから完全隔離」と説明しない。[NET-01]

今回は両アプリを同一所有者が管理する個人用構成として受け入れる。ログイン・ログアウトの干渉、Cookie名や属性、ログへ認証情報を出していないかを確認する。問題があれば新規側の公開を止めて分離方法を再検討し、既存OpenClawを無断で書き換えない。

### 6.4 OSの権限境界

専用venvはPython依存関係を分けるが、OSレベルのSandboxではない。Ollama、Open WebUI、既存OpenClawが同じOSユーザーで動く場合、そのユーザー権限・ホーム領域のリスクを共有する。

新規アプリには不要なフルディスクアクセスを与えず、Tools・Functions・コード実行・外部ツール接続を追加しない。WebUIには秘密鍵を含まない試験文書から投入する。高度なホスト分離が必要な企業配布は本構成の対象外。[OW-05]

### 6.5 外部通信境界

Ollamaのクラウド機能を停止し、Open WebUIの外部AI、外部埋め込み、外部OCR、Web検索を使わない。エラー時にも外部モデルへのフォールバックを設定しない。[OL-02]

OFFLINE_MODEはパッケージ取得や更新チェック等を抑制する設定であり、全通信を遮断する仕組みではない。導入時の取得通信、Tailscaleの制御通信、通常のローカル推論を区別する。[OW-03]

試験時には、設定値に加えてプロセスの通信先・ログを確認する。これを「OS全体で外部通信が一切ない証明」とは扱わない。既存OpenClawの通信を巻き込むファイアウォール変更は行わない。

## 7. 導入・初期化の順序

### 段階0：読み取り専用の現状調査

Mac mini上でチップ、メモリ、OS、ディスク、既存Ollama、Python/uv、待受ポート、LaunchAgent、Tailscale/Serveを確認する。

```bash
# 代表的な読み取り確認。秘密情報を含む出力は公開しない。
uname -m
sw_vers
sysctl -n hw.memsize
df -h "$HOME"
command -v ollama
command -v uv
lsof -nP -iTCP:11434 -sTCP:LISTEN
lsof -nP -iTCP:3000 -sTCP:LISTEN
lsof -nP -iTCP:9443 -sTCP:LISTEN
lsof -nP -iTCP:18789 -sTCP:LISTEN
tailscale status
tailscale serve status
tailscale serve status --json
```

`lsof`に9443番が表示されないだけではServe側の未使用を断定しない。Serveの設定も必ず確認する。既存サービスの起動元が不明な場合は、ポートを空ける目的で強制終了しない。

9月6日の既存OpenClawレポートは調査の手がかりに使い、現在値は実機を正とする。[U-02]

### 段階1：Ollama単体の確認

公式macOS版を導入し、起動元を1つにする。loopback、クラウド停止、並列数の初期値を設定する。軽量なローカルモデルで日本語の一往復を確認し、ハード・モデル・速度を記録する。

```bash
# Ollamaが起動した後の読み取り確認例。
curl --fail --silent --show-error http://127.0.0.1:11434/api/tags
curl --fail --silent --show-error http://127.0.0.1:11434/api/ps
```

この段階ではOllamaをTailnetへ公開しない。GPU利用はApple Siliconであることと、実際のOllamaの状態・ログを確認して判断する。[OL-01][OL-03][OL-06]

### 段階2：WebUIをMac mini内部だけで初期化

専用venv、固定DATA_DIR、ローカル生成したWEBUI_SECRET_KEYを用意する。初期URLとOriginはMac mini自身のローカル確認用とし、WebUIを`127.0.0.1:3000`で起動する。

この間だけ管理者作成用の新規登録を許可し、通常運用のHTTPS用Secure Cookie設定とは分ける。本人がMac miniのブラウザで管理者アカウントを作成する。パスワードをチャット・コマンド引数・ログに書かない。

最初に作成されるアカウントは管理者になるため、まだ9443番のServeを追加しない。初回登録画面を他端末へ出してはいけない。[OW-08]

### 段階3：登録を閉じてローカル接続を確定

管理画面で新規登録を閉じ、環境ファイルの通常運用値もfalseへ揃える。外部接続、Web検索、ツール、コード実行を点検する。Ollama接続先はバックエンドの`http://127.0.0.1:11434`だけにする。

既存の永続設定が残っていないことを管理画面で確認する。ローカル埋め込みモデルと依存物を準備し、TXT・MD・テキストPDFを使って最低限の文書処理を確認する。必要な取得が終わった段階でOFFLINE_MODE等を有効化する。

### 段階4：HTTPS経路を追加

正規URLを`https://<MAC_MINI_FQDN>:9443`に決め、WEBUI_URLとCORS_ALLOW_ORIGINの環境値・永続値を合わせる。CookieをHTTPS運用の値へ変更してWebUIを再起動する。

そのうえで、9443番が既存用途に使われていないことを再確認して追加する。

```bash
tailscale serve --bg --https=9443 http://127.0.0.1:3000
tailscale serve status
```

出力された実際のURLを構成記録へ保存する。証明書警告を無視して進めない。ローカルHTTPがSecure Cookie設定後に管理操作へ使えなくなった場合も、通常利用のHTTPS設定を安易に弱めない。[OW-04][TS-01]

### 段階5：MacBookから別回線で確認

MacBookからTailscale経由で開き、ログイン、日本語チャット、モデル切替、履歴継続、添付を確認する。その後、別Wi-Fiやテザリングに切り替えて同じ試験を行う。

OpenClawを同じブラウザ・同じ端末で開き、従来の接続が続くことを確認する。可能な保守タイミングでOpenClaw再起動後も両方のServe経路が残ることを検証する。動作確認のために既存セッションや作業を無断で止めない。

### 段階6：起動管理と復元試験

手動起動を終了し、専用LaunchAgentによる起動へ切り替える。ターミナルを閉じても利用できること、Mac再起動・ログイン後に復帰すること、保存データが残ることを確認する。

初期バックアップを取り、隔離した復元先で会話・添付・設定を確認する。要件定義のAT結果を記録して導入完了とする。

## 8. 起動・停止とライフサイクル

### 8.1 通常の起動フロー

```text
Mac miniの電源ON
    ↓
必要に応じてFileVault解除・macOSログイン
    ↓
既存Tailscaleの稼働・接続
    ↓
専用LaunchAgentがOllamaを起動
    ↓
WebUI起動ラッパーがOllama APIの準備を確認
    ↓
専用venvからOpen WebUIを単一workerで起動
    ↓
永続化済みServe経路からHTTPS利用可能
```

LaunchAgentはユーザーログイン後の起動として設計する。Ollama側・WebUI側それぞれのジョブを独立させるが、WebUIラッパー側に有限回の準備確認を持たせる。モデルの事前ロードは必須にしない。[OS-01]

Serveの`--bg`による再開だけではWebUIとOllamaは起動しない。独立した3要素として試験する。[TS-01]

### 8.2 状態ごとの利用可否

| 状態 | 期待される扱い |
|---|---|
| Mac mini稼働・ユーザーログイン・Tailscale接続 | 通常利用可能 |
| 画面ロックのみ | サービス継続を期待し、実機試験する |
| システムスリープ | 遠隔利用を保証しない。運用時のスリープ条件を確認 |
| ユーザーログアウト | LaunchAgent稼働を保証しない |
| 再起動後・FileVault未解除 | サービス利用を前提にしない |
| Mac mini電源OFF・ネット切断 | 遠隔利用不可 |
| MacBookネット切断 | 遠隔利用不可。MacBook単体にモデルが移るわけではない |
| Ollamaのみ停止 | WebUIが開いても生成できない |
| WebUIのみ停止 | Serveが残っていても利用できない |

電源・スリープ設定を変更する場合は内容を説明してから行う。FileVault無効化・自動ログイン・セキュリティ緩和を、自動復旧のための近道として採用しない。

### 8.3 保守停止

WebUIの保守では、まず新しい生成・添付処理を止め、LaunchAgentの再起動がかからない状態でジョブを停止する。単にプロセスを終了させてKeepAliveで戻ってくる状態のまま、バックアップや更新をしない。

停止対象は本プロジェクトのジョブだけ。`killall python`、`killall node`、Docker全体の停止、Tailscaleの全体停止は使わない。

## 9. 障害と切り分け

| 症状 | 最初に確認すること | 対応の原則 |
|---|---|---|
| 正規URLが開かない | 両端末のTailscale、FQDN、Serve状態、WebUI起動 | 既存443番を変更せず新規経路を診断 |
| 画面だけ開いて返信なし | Ollamaの一覧API、接続先、モデル取得、Origin、ログ | CORSを`*`にして回避しない |
| モデル一覧が空 | Ollamaが起動しているか、保存接続先、モデル有無 | localhostの端末取り違えを確認 |
| ローカルなのに外部接続がある | 外部プロバイダー、検索、埋め込み、拡張、更新確認 | 用途を特定。OFFLINE_MODEだけで安全と判断しない |
| 添付を参照できない | 対応形式、抽出結果、埋め込みモデル、索引 | 外部OCR等へ勝手に切り替えない |
| 返信が遅い | コールド/ウォーム、文脈長、メモリ、OpenClaw併用 | モデル・生成量・文脈長を減らし再測定 |
| 再起動後に動かない | ユーザーログイン、LaunchAgent、絶対パス、env、ログ | FileVaultや自動ログインを安易に変更しない |
| 設定変更が効かない | DBに保存された永続設定、実行プロセスのenv | DATA_DIRの削除でリセットしない |
| 履歴が消えたように見える | DATA_DIR、アカウント、別venv/別インスタンス | 元データを消さず正しい保存先へ戻す |
| OpenClawへ影響が出た | 443番経路、資源負荷、Cookie、Serve自動管理 | 新規側を停止して元の動作を保護 |

トラブルの最小切り分け順は「Ollama単体 → WebUIのローカル待受 → Serve → 別端末」とする。最初から複数の設定を同時に変えない。

## 10. バックアップ・復元・ロールバック

### 10.1 バックアップ取得

1. 日時、Open WebUI/Ollama/Pythonの版、モデル情報を記録する。
2. 新しい会話・添付処理を止め、Open WebUIのジョブを停止する。
3. プロセスが停止したことを確認してから、DATA_DIR全体をコピーする。
4. `config`、起動スクリプト、LaunchAgent定義、`records`を同じ復元単位へ保存する。
5. バックアップに秘密情報が含まれることを前提に、アクセス権と別媒体保管を管理する。
6. WebUIを再起動し、通常運用を確認する。

稼働中SQLiteの単純コピーを標準手順にしない。停止が許容できる個人用構成なので、最初は停止中バックアップで単純化する。[OW-07]

### 10.2 復元試験

元のデータへいきなり上書きせず、コピーしたDATA_DIRと対応版のアプリを、別のローカルディレクトリ・未使用ポートで一時起動する。本番DATA_DIRを2プロセスで共有しない。

復元試験はloopbackのみで行い、Serve経路を追加しない。URL/Origin/Cookieをローカル試験用へ調整する場合は、復元コピー側だけに適用する。会話、設定、添付参照、秘密鍵の整合性を確認し、試験用インスタンスを停止する。

別媒体へのバックアップが未整備なら、Mac mini故障時の復旧は未検証と記録する。モデル本体を再取得する方針なら、同じモデルを取得できるかも確認する。

### 10.3 更新失敗時

WebUIのジョブを停止し、失敗した状態を退避する。旧版に対応する環境と更新前DATA_DIR、秘密鍵・設定を一組として戻す。DB移行があった場合、パッケージのダウングレードだけで復元しない。[OW-07]

### 10.4 新規環境全体を取りやめる場合

止めるのは本プロジェクトの2ジョブと、新しく追加したServe入口だけである。既存Ollamaが共有利用されていた場合は、その利用を確認してから停止対象を判断する。

```bash
# 9443番が「今回追加した専用入口」であることを確認した場合だけ使う。
# 実機の登録フラグと採用Tailscale版のヘルプに合わせる。
tailscale serve --bg --https=9443 off
tailscale serve status
```

Serveでは、元の設定に対応するフラグと`off`で個別経路を無効にできる。全体を消す`tailscale serve reset`は使わない。[TS-01]

取りやめる場合もDATA_DIR、モデル、秘密鍵、バックアップは自動削除しない。アンインストールやデータ削除は別の明示的な操作として扱う。

## 11. 主要な設計判断

| 判断ID | 決定 | 理由・結果 |
|---|---|---|
| ADR-01 | Mac miniを唯一の本体とする | 端末間DB同期を不要にし、履歴・モデルを集約する |
| ADR-02 | OllamaとWebUIの両方をmacOS直接実行 | ユーザー承認済み。推論API接続をloopbackに統一 |
| ADR-03 | Open WebUI標準機能を使う | 自作UI・独自バックエンド・フォークを不要にする |
| ADR-04 | Serveの別HTTPSポートを追加 | 既存OpenClawの443番を維持し、パス書換えを避ける |
| ADR-05 | 標準WebUIログインを残す | Tailnet到達性とアプリ認証を分ける |
| ADR-06 | SQLite + ローカルChroma、1worker | 個人用の単一インスタンスとして運用を簡単にする |
| ADR-07 | ローカル埋め込み・標準文書処理 | 添付処理を外部サービスへ依存させない |
| ADR-08 | ユーザーLaunchAgent | ログイン後に使える個人環境とし、root/無人復旧構成を追加しない |
| ADR-09 | DATA_DIRとvenvを分離 | 更新・環境作り直しとデータ保持を両立する |
| ADR-10 | 拡張・コード実行を追加しない | 初期用途をチャットと文書参照に限定する |

これらを将来変更するときは、困りごと・変更範囲・既存環境への影響・受け入れ試験を明記する。将来の販売や機能拡張のためだけに、現在不要な機能を先に実装しない。

## 12. 実装担当者への引き渡し条件

実装は要件定義のAT-01から順に進める。最終的に、利用URL、起動停止方法、保存先、モデル一覧、採用版、バックアップ先、AT結果、残っている制約を本人が確認できる記録へまとめる。

**絶対にしないこと：** 既存ポートを使うプロセスの強制停止、OpenClawの認証・Gateway・Sandboxの変更、Serve全体リセット、0.0.0.0でのAPI公開、Funnel有効化、外部LLMへの自動退避、venvをSandboxとみなす説明、実測していない性能・安全性の断定。

最初の到達点は「Mac mini内部で一往復」、次が「MacBookの別回線から一往復」、最後が「履歴・添付・認証・復帰・復元・共存の確認」である。

## 13. 出典・根拠

Web資料は2026年9月16日に確認。以下は方式と制約を裏付ける資料であり、BOSSの実機での検証結果ではない。過去の接続記録と現在の実機状態も区別する。

- **[U-02]** ユーザー過去資料：`OpenClaw_MacMini_MacBook_Tailscale_remote_setup_report_2026-09-06.md`。現在値は導入前に読み取り確認する。
- **[OL-01]** Ollama / macOS：<https://docs.ollama.com/macos>
- **[OL-02]** Ollama / FAQ：<https://docs.ollama.com/faq>
- **[OL-03]** Ollama / List models：<https://docs.ollama.com/api/tags>
- **[OL-04]** Ollama / Generate embeddings：<https://docs.ollama.com/api/embed>
- **[OL-05]** Ollama / Generate a chat message：<https://docs.ollama.com/api/chat>
- **[OL-06]** Ollama / List running models：<https://docs.ollama.com/api/ps>
- **[OW-01]** Open WebUI / Python environments：<https://docs.openwebui.com/getting-started/quick-start/install-methods/python-environments/>
- **[OW-02]** Open WebUI / Ollama connection：<https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-ollama/>
- **[OW-03]** Open WebUI / Environment Variable Configuration：<https://docs.openwebui.com/reference/env-configuration/>
- **[OW-04]** Open WebUI / HTTPS using Tailscale：<https://docs.openwebui.com/reference/https/tailscale/>
- **[OW-05]** Open WebUI / Hardening：<https://docs.openwebui.com/getting-started/advanced-topics/hardening/>
- **[OW-07]** Open WebUI / Updating：<https://docs.openwebui.com/getting-started/updating/>
- **[OW-08]** Open WebUI / Quick Start, admin account：<https://docs.openwebui.com/getting-started/quick-start/>
- **[TS-01]** Tailscale / Serve command：<https://tailscale.com/docs/reference/tailscale-cli/serve>
- **[TS-02]** Tailscale / Serve：<https://tailscale.com/docs/features/tailscale-serve>
- **[OS-01]** Apple / Creating Launch Daemons and Agents：<https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html>
- **[NET-01]** RFC 6265 / HTTP State Management Mechanism, §8.5：<https://www.rfc-editor.org/rfc/rfc6265>
