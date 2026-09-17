# Local LLM Web UI 構築手順書

> Ollama + Open WebUI + Tailscale Serve を macOS 上で直接動かし、Mac mini をローカルLLMサーバーとして利用するための構築ガイド

- 対象: macOS / Apple Silicon を中心とした個人利用
- 想定構成: Ollama は macOS ネイティブ、Open WebUI は専用 Python 環境、遠隔アクセスは Tailscale Serve
- 本書の対象バージョン: `local-llm-web-mvp 0.1.x`
- 更新日: 2026-09-17

---

## 1. このプロジェクトの目的

このプロジェクトは、Mac mini 上で動くローカルLLMを、MacBook・スマートフォン・タブレットなどからブラウザで使えるようにするための導入・運用ラッパーです。

新しいチャットUIを独自実装するのではなく、UIには **Open WebUI**、モデル実行には **Ollama**、遠隔アクセスには **Tailscale Serve** を使います。

狙いは次の3点です。

1. Ollama のAPIをインターネットへ直接公開しない
2. Open WebUIだけをTailnet内へHTTPS公開する
3. 既存のサービスやポートを勝手に停止・上書きせず、安全側に失敗する

本プロジェクトは、Open WebUI・Ollama・Tailscaleそのものを再配布する製品ではありません。各ソフトウェアは利用者のMacに公式配布元から導入されます。

---

## 2. 全体アーキテクチャ

```text
MacBook / Pixel / iPad
        │
        │ HTTPS / Tailscale
        ▼
https://<mac-host>.<tailnet>.ts.net:<HTTPS_PORT>
        │
        ▼
Tailscale Serve
        │
        │ proxy
        ▼
Open WebUI
127.0.0.1:<WEBUI_PORT>
        │
        │ Ollama API
        ▼
Ollama
127.0.0.1:11434
        │
        ├─ Chat model
        └─ Embedding model
```

### 2.1 原則

- Ollama は `127.0.0.1:11434` のみで待ち受ける
- Open WebUI も `127.0.0.1:<WEBUI_PORT>` のみで待ち受ける
- 外部端末からの入口は Tailscale Serve のHTTPSだけにする
- Tailscale Funnelは使わない
- macOSのポートフォワーディングやルーターのポート開放は使わない
- 既存のServe設定を `reset` しない

Tailscale ServeはTailnet内のローカルサービスを共有する機能で、Funnelはインターネット全体へ公開する機能です。本構成ではServeのみを使います。

---

## 3. 対応範囲と非対応範囲

### 対応

- macOS
- Apple Silicon
- Ollama.app または Ollama CLI
- Open WebUI のPython版
- Tailscale Serve
- 個人利用または少人数での検証
- ローカルチャット
- ローカル埋め込み
- TXT / Markdown / テキストPDFなどの文書利用
- 会話履歴のローカル保存
- 起動・停止・診断・バックアップ

### 非対応 / 未検証

- Windows / Linux の自動構築
- Docker版Open WebUI
- Kubernetes
- インターネットへの公開
- Tailscale Funnel
- 複数組織向けの認証設計
- SSO
- 大規模マルチユーザー運用
- SLAを伴う業務システム
- 医療・金融など規制対象データへの適合性保証
- Open WebUI / Ollama の脆弱性監査そのもの

---

## 4. 参考検証環境

本プロジェクトは、少なくとも以下の1環境で導入フローを確認しています。

```text
macOS: 27.0
Architecture: arm64
Chip: Apple M4 Pro
Memory: 64 GB
Python: 3.11.15
Open WebUI: 0.11.3
Ollama: macOSアプリ版
Tailscale: macOS上で稼働
```

Open WebUI導入時には、専用Python 3.11.15環境に263パッケージが入り、依存関係チェックが完了しています。

この環境は**推奨最低要件ではありません**。使用するモデルサイズ、コンテキスト長、同時実行数によって必要メモリは大きく変わります。

---

## 5. 必要ソフトウェア

### 必須

- macOS
- Ollama
- Tailscale
- `uv`
- Python 3.9以上（ランチャー用）
  - Open WebUI本体用にはプロジェクトがPython 3.11環境を別途用意します

### 推奨

- Apple Silicon
- 16 GB以上のユニファイドメモリ
- 十分な空きストレージ
- FileVault

Open WebUI公式はPython 3.11 / 3.12をサポートし、特にPython 3.11を重点的にテストしています。

---

## 6. GitHubからの導入

リポジトリ名を仮に `local-llm-web-mvp` とします。

```bash
git clone https://github.com/<OWNER>/local-llm-web-mvp.git
cd local-llm-web-mvp
```

実行権限が失われている場合のみ:

```bash
chmod +x llmweb setup.command
```

`sudo`は使いません。

---

# 7. 導入手順

## Step 0. 既存環境を確認する

最初に必ずpreflightを実行します。

```bash
bash ./llmweb preflight --https-port 9443
```

例:

```json
{
  "platform": "Darwin",
  "architecture": "arm64",
  "port_3000": "待受なし",
  "port_9443": "待受なし",
  "port_11434": "使用中",
  "ollama_listener_endpoints": [
    "127.0.0.1:11434"
  ],
  "ollama_loopback_only": true,
  "serve_9443_in_use": false
}
```

### 合格条件

- WebUI用ポートが空いている
- HTTPS公開用ポートがTailscale Serveで未使用
- Ollama再利用時は `127.0.0.1:11434` のみ
- Tailscaleが `Running`

`9443`が使用中なら、既存サービスを止めず、別のHTTPSポートを選びます。

例:

```bash
bash ./llmweb preflight --https-port 10443
```

---

## Step 1. Ollamaの待受を確認する

```bash
lsof -nP -iTCP:11434 -sTCP:LISTEN
```

安全な例:

```text
ollama ... TCP 127.0.0.1:11434 (LISTEN)
```

避ける例:

```text
ollama ... TCP *:11434 (LISTEN)
ollama ... TCP 0.0.0.0:11434 (LISTEN)
```

### Ollama.app利用時

Ollamaの設定で **Expose Ollama to the network** をOFFにします。

環境変数も確認します。

```bash
launchctl getenv OLLAMA_HOST
```

何も表示されない状態が基本です。

macOSアプリ版Ollamaで環境変数を使う場合は `launchctl setenv` が公式手順ですが、本構成ではOllamaを外部へ公開しないため、`OLLAMA_HOST=0.0.0.0:11434` のような設定は使いません。

---

## Step 2. Open WebUI専用環境を作る

既にOllama.appが動いている場合:

```bash
bash ./llmweb install \
  --https-port 9443 \
  --ollama-mode existing
```

Ollamaもこのプロジェクト側で起動管理する構成では `managed` モードを使えますが、既にOllama.appを普段使いしているMacでは `existing` を推奨します。

### `existing` の意味

- 既存Ollamaを停止しない
- 既存Ollamaを再起動しない
- 既存OllamaのLaunchAgentを変更しない
- `127.0.0.1:11434`で応答することだけ確認する

### installが行うこと

概ね以下を実行します。

1. 専用データディレクトリ作成
2. 専用Python 3.11環境作成
3. Open WebUIの固定版をインストール
4. 依存関係をロック
5. ハッシュ付きrequirementsを保存
6. Open WebUI設定ファイルを生成
7. LaunchAgent用ファイルを生成
8. 実際に導入されたバージョンを記録

保存先:

```text
~/Library/Application Support/local-llm-web/
```

代表的な構造:

```text
local-llm-web/
├── venv/
├── data/
├── config/
│   ├── state.json
│   ├── webui.env
│   ├── requirements.in
│   └── requirements.lock
├── scripts/
├── logs/
├── records/
└── backups/
```

---

## Step 3. モデルを取得する

初期設定の例:

```text
Chat model: qwen3:4b
Embedding model: bge-m3:latest
```

取得:

```bash
bash ./llmweb pull-models
```

モデルは利用者のOllama環境へ保存されます。

GitHubリポジトリにはモデル重みを含めないでください。

---

## Step 4. Ollama APIをスモークテストする

```bash
bash ./llmweb smoke
```

確認内容:

- `/api/chat` が応答する
- `/api/embed` が応答する
- 埋め込みベクトルが返る

例:

```json
{
  "chat_seconds": 2.65,
  "embedding_dimensions": 1024
}
```

ここではOpen WebUIのUI・ストリーミング・文書検索までは検証しません。

---

## Step 5. Open WebUIをローカル起動する

```bash
bash ./llmweb start webui
```

Mac mini自身のブラウザから開きます。

```text
http://127.0.0.1:3000
```

この時点ではTailscaleへ公開しません。

---

## Step 6. 管理者アカウントを作る

初回画面から自分用の管理者アカウントを1つ作成します。

重要:

- 初期アカウント作成はMac miniのloopbackから行う
- 管理者作成前に遠隔公開しない
- GitHubのREADME、Issue、ログへメールアドレスやパスワードを書かない

---

## Step 7. チャットをローカル確認する

モデルとして `qwen3:4b` などを選び、短いメッセージを送ります。

日本語をデフォルトにしたい場合はOpen WebUIのSystem Promptへ、例えば次を設定します。

```text
原則として日本語で回答してください。
ユーザーが明示的に他の言語を指定した場合のみ、その言語で回答してください。
```

言語設定はネットワーク接続の成否とは別です。

---

## Step 8. 公開前の安全設定を確定する

管理者作成後に実行します。

```bash
bash ./llmweb secure
```

管理者メールとパスワードをターミナルで入力します。

### secureで行う主な処理

- 管理者認証を確認
- 新規ユーザー登録を停止
- ローカルOllama接続を固定
- 外部OpenAI互換APIを無効化
- Web検索を無効化
- コード実行を無効化
- Functions / Plugins / runtime pip install系を無効化
- RAG埋め込み先をOllamaへ設定
- `WEBUI_URL`を最終HTTPS URLへ設定
- HTTPS用Cookieへ切り替え
- CORSを最終HTTPS URLへ制限
- 保存後に設定を読み戻して確認

### 重要な挙動

`secure` 実行後は、CookieがHTTPS前提になるため、

```text
http://127.0.0.1:3000
```

でのチャットが一時的に正常動作しなくなる場合があります。

これは想定動作です。

次の `publish` でHTTPS入口を有効化し、その後はHTTPS URLから利用します。

---

## Step 9. Tailscale ServeでHTTPS公開する

```bash
bash ./llmweb publish
```

構成例:

```text
https://<mac-host>.<tailnet>.ts.net:9443
        ↓
http://127.0.0.1:3000
```

このプロジェクトは、既存Serve設定を確認した上で新しいHTTPSポートだけを追加します。

### やってはいけないこと

```bash
tailscale serve reset
```

既存のOpenClaw、監視サービス、開発サーバーなど別用途のServe設定まで消える可能性があります。

手動で確認する場合:

```bash
tailscale serve status --json
```

Tailscale ServeはTailnet内だけに公開されます。インターネット全体へ公開するFunnelとは別機能です。

---

## Step 10. 診断する

```bash
bash ./llmweb doctor
```

主に以下を確認します。

- Open WebUIプロセス
- loopback待受
- Ollama到達性
- Open WebUI認証
- 未認証APIの拒否
- Tailscale Serve経路
- 期待するHTTPS URL

---

## Step 11. 別端末からアクセスする

MacBook / Pixel / iPadなど、同じTailnetに接続した端末から:

```text
https://<mac-host>.<tailnet>.ts.net:9443
```

を開きます。

確認項目:

- ログインできる
- モデル一覧が表示される
- チャットできる
- ストリーミング表示される
- 履歴が保持される
- Tailscaleを切るとアクセスできなくなる

---

# 8. 日常利用

日常的には次の4つが動いていれば利用できます。

1. Mac mini
2. Ollama
3. Open WebUI
4. Tailscale

ブラウザでは常にHTTPS URLを使います。

```text
https://<mac-host>.<tailnet>.ts.net:<HTTPS_PORT>
```

## 自動起動

Open WebUIはmacOSのLaunchAgentで起動する構成です。

LaunchAgentはmacOSのユーザーログインセッションで動きます。

そのため:

- Mac mini電源ONだけでは十分でない場合がある
- FileVault解除が必要
- macOSユーザーへのログインが必要
- スリープ中は利用できない場合がある

GitHub上で「再起動後も必ず無人で使える」と断定しないでください。

---

# 9. 起動・停止

Open WebUI:

```bash
bash ./llmweb start webui
bash ./llmweb stop webui
```

全サービス管理モードの場合:

```bash
bash ./llmweb start all
bash ./llmweb stop all
```

`--ollama-mode existing`の場合、既存Ollamaは本プロジェクトの所有物ではないため、このCLIで停止しません。

---

# 10. Tailscale公開を解除する

```bash
bash ./llmweb unpublish
```

本プロジェクトが作成したと確認できる専用経路だけを削除します。

既存Serve設定をまとめて消さない設計が重要です。

---

# 11. バックアップ

```bash
bash ./llmweb backup
```

バックアップ対象の例:

- Open WebUI SQLite DB
- 会話履歴
- 添付ファイル
- Chromaベクトルデータ
- 設定
- プロジェクト側の記録

バックアップには秘密情報を含む可能性があります。

GitHubへコミットしないでください。

---

# 12. 復元試験

本番を直接上書きせず、隔離コピーで確認します。

```bash
bash ./llmweb restore-test \
  "$HOME/Library/Application Support/local-llm-web/backups/<BACKUP_NAME>"
```

復元試験用ポートは本番とは分けます。

確認項目:

- SQLite整合性
- 会話履歴
- 添付文書
- 設定
- 文書検索

---

# 13. Open WebUIの保存設計

Open WebUIは`DATA_DIR`を明示して利用します。

この設定を省略すると、実行方式によってはデータ保存先が分かりにくくなります。

本プロジェクトでは:

```text
~/Library/Application Support/local-llm-web/data
```

を固定保存先にします。

Open WebUI公式もPython/uv利用時には`DATA_DIR`を明示するよう案内しています。

---

# 14. RAG / 文書検索

初期構成では埋め込みもローカルへ寄せます。

```text
RAG_EMBEDDING_ENGINE=ollama
RAG_EMBEDDING_MODEL=<embedding-model>
```

例:

```text
bge-m3
nomic-embed-text
```

Open WebUI自身のSentenceTransformersを使わず、Ollamaへ埋め込みを任せることで、構成と処理先を分かりやすくします。

個人利用の初期構成では、SQLite + ローカルChromaの単一ワーカー運用を前提にします。

---

# 15. 主なセキュリティ設定

以下は考え方を示したものであり、最終的な正確な設定一覧はソースコードを正としてください。

| 項目 | 方針 |
|---|---|
| Ollama | `127.0.0.1:11434`限定 |
| Open WebUI | `127.0.0.1`限定 |
| 遠隔入口 | Tailscale Serve |
| HTTPS | Tailscale証明書 |
| Funnel | 使用しない |
| Sign up | 管理者作成後OFF |
| CORS | 利用するHTTPS URLへ限定 |
| Secure Cookie | HTTPS移行後ON |
| Web検索 | 初期構成ではOFF |
| コード実行 | OFF |
| Runtime pip install | OFF |
| 外部API | 初期構成ではOFF |
| RAG embeddings | Ollama |
| Workers | 1 |

Open WebUI公式も、本番運用ではCORS制限、HTTPS用Secure Cookie、runtime pip installの停止などを推奨しています。

---

# 16. 依存関係の再現性

Open WebUI本体は固定バージョンを指定します。

例:

```text
open-webui==0.11.3
```

`uv pip compile`で依存関係を解決し、ハッシュ付きロックを作成します。

概念例:

```bash
uv pip compile \
  requirements.in \
  --generate-hashes \
  --output-file requirements.lock
```

インストールはロックファイルへ同期します。

```bash
uv pip sync requirements.lock
```

GitHub配布時には、macOS/arm64で生成したロックを他OSへ無条件に適用できるとは限らない点を明記してください。

---

# 17. LaunchAgentによる自動起動

Open WebUIの自動起動は `~/Library/LaunchAgents/` を利用します。

概念例:

```xml
<dict>
  <key>Label</key>
  <string>local.llmweb.webui</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/absolute/path/start-webui.sh</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
</dict>
```

ポイント:

- `ProgramArguments`は絶対パス
- root権限を使わない
- 本プロジェクト所有のLabelだけを操作する
- 既存LaunchAgentを名前だけで停止しない

---

# 18. トラブルシューティング

## 18.1 `11434` が使用中

```bash
lsof -nP -iTCP:11434 -sTCP:LISTEN
```

Ollamaなら、`existing`モードで再利用できます。

不明なプロセスの場合は停止せず、先に所有者を調査してください。

---

## 18.2 Ollamaが `*:11434` になっている

Ollama.appの **Expose Ollama to the network** をOFFにし、再起動します。

```bash
launchctl getenv OLLAMA_HOST
```

も確認します。

---

## 18.3 HTTPSポートが使用中

```bash
tailscale serve status --json
```

既存ポートを削除せず、新規側を変更します。

```bash
bash ./llmweb preflight --https-port 10443
bash ./llmweb install --https-port 10443 --ollama-mode existing
```

---

## 18.4 `secure`後にlocalhostでチャットできない

想定される挙動です。

`secure`後はHTTPS用CookieとCORSに切り替わっています。

```bash
bash ./llmweb publish
bash ./llmweb doctor
```

を完了し、HTTPS URLを利用します。

---

## 18.5 MacBookからアクセスできない

確認順:

1. Mac miniが起動中か
2. Mac miniがログイン済みか
3. Mac miniのTailscaleがConnectedか
4. MacBookのTailscaleがConnectedか
5. Open WebUIが起動中か
6. `tailscale serve status --json`
7. Tailnet ACL / Grants
8. `bash ./llmweb doctor`

---

## 18.6 UIは出るがモデルが出ない

```bash
ollama list
curl http://127.0.0.1:11434/api/tags
```

Open WebUIからOllamaへの接続設定も確認します。

---

# 19. GitHub配布用の推奨リポジトリ構成

```text
local-llm-web-mvp/
├── README.md
├── LICENSE
├── NOTICE.md
├── CHANGELOG.md
├── SECURITY.md
├── .gitignore
├── llmweb
├── llmweb.py
├── core.py
├── setup.command
├── docs/
│   ├── BUILD_GUIDE.md
│   ├── OPERATIONS.md
│   ├── ACCEPTANCE.md
│   ├── IMPLEMENTATION_STATUS.md
│   └── SOURCES.md
├── specs/
│   ├── 01_requirements.md
│   ├── 02_technology-stack.md
│   └── 03_architecture.md
├── samples/
│   ├── sample.md
│   └── expected-answers.json
└── tests/
    └── ...
```

トップのREADMEは短くし、詳細は本書へリンクする形を推奨します。

---

# 20. GitHubへ絶対に入れないもの

`.gitignore`で最低限、次を除外します。

```gitignore
__pycache__/
*.pyc
.DS_Store

.env
*.env
state.json

venv/
.venv/

data/
logs/
records/
backups/
restore-tests/

*.db
*.db-wal
*.db-shm
```

加えて、以下を手動確認します。

- メールアドレス
- Tailscale FQDN
- Tailnet名
- IPアドレス
- APIキー
- `WEBUI_SECRET_KEY`
- Cookie / JWT
- バックアップ
- 実会話
- 実ファイル
- 患者・顧客情報

公開前には以下を推奨します。

```bash
git grep -n -E 'gmail|ts\.net|API_KEY|SECRET|PASSWORD|TOKEN'
```

検出結果は人間が確認してください。

---

# 21. ライセンスとOpen WebUIの扱い

本リポジトリ自身のコードには、作者が配布方針に合ったLICENSEを明示してください。

一方、Open WebUIは第三者ソフトウェアです。

本プロジェクトでは次の方針を推奨します。

- Open WebUI本体をリポジトリへコピーしない
- `pip` / `uv` で公式配布物を取得する
- Open WebUIのロゴ・名称を勝手に消さない
- 自分のプロジェクトを「Open WebUI公式」と誤認させない
- `NOTICE.md` で利用している第三者ソフトウェアを明記する
- Open WebUIの現行ライセンスをRelease前に再確認する

Open WebUIの現行ライセンスにはブランド表示に関する条件があります。再配布・商用利用・リブランドを行う場合は、必ず公式LICENSEの最新版を確認してください。

これは法的助言ではありません。

---

# 22. READMEに載せる最短導入例

GitHubトップページには、詳細な説明を全部置かず、次程度に絞ると使いやすくなります。

```bash
# 1. clone
git clone https://github.com/<OWNER>/local-llm-web-mvp.git
cd local-llm-web-mvp

# 2. check
bash ./llmweb preflight --https-port 9443

# 3. install
bash ./llmweb install --https-port 9443 --ollama-mode existing

# 4. models
bash ./llmweb pull-models
bash ./llmweb smoke

# 5. local WebUI
bash ./llmweb start webui
# Open: http://127.0.0.1:3000

# 6. create admin in browser, then harden
bash ./llmweb secure

# 7. Tailscale HTTPS
bash ./llmweb publish
bash ./llmweb doctor
```

詳細は `docs/BUILD_GUIDE.md` へ誘導します。

---

# 23. リリース前チェックリスト

GitHub Releaseを作る前に以下をすべて確認します。

- [ ] `preflight` が既存環境を変更しない
- [ ] 既存Ollamaを `existing` モードで停止しない
- [ ] Ollamaのwildcard待受を拒否する
- [ ] 使用中HTTPSポートを上書きしない
- [ ] 既存Tailscale Serve経路を保持する
- [ ] `tailscale serve reset` を使わない
- [ ] 管理者作成前に遠隔公開しない
- [ ] `secure`で新規登録が停止する
- [ ] `publish`前後のServe設定を比較する
- [ ] `doctor`が成功する
- [ ] Tailscale OFFの端末から到達できない
- [ ] Open WebUIが再起動後も復帰する
- [ ] Ollamaが再起動後もloopback限定
- [ ] バックアップを作成できる
- [ ] 隔離復元試験が成功する
- [ ] `.gitignore`に秘密データが含まれる
- [ ] Git履歴に秘密情報が残っていない
- [ ] サンプルデータが架空情報のみ
- [ ] LICENSE / NOTICE / SECURITY.mdを確認
- [ ] Open WebUIの現行LICENSEを確認
- [ ] CHANGELOGを更新
- [ ] タグとコード内バージョンが一致

---

# 24. 推奨受け入れ試験

最低限、次を手動で確認します。

### ローカル

- [ ] Ollama Chat API
- [ ] Ollama Embed API
- [ ] Open WebUI表示
- [ ] 日本語チャット
- [ ] ストリーミング
- [ ] 会話履歴
- [ ] モデル切替
- [ ] TXT添付
- [ ] Markdown添付
- [ ] テキストPDF添付

### 遠隔

- [ ] 別Wi-FiのMacBookから利用
- [ ] スマホから利用
- [ ] Tailscale OFFで利用不可
- [ ] HTTPS証明書エラーなし
- [ ] CORSエラーなし

### 復帰

- [ ] Mac mini再起動
- [ ] macOSログイン後にOpen WebUI復帰
- [ ] Ollama復帰
- [ ] Tailscale復帰
- [ ] URLが同じ
- [ ] 履歴が残る

### バックアップ

- [ ] バックアップ作成
- [ ] SQLite整合性
- [ ] 隔離復元
- [ ] 過去会話表示
- [ ] 添付文書表示

---

# 25. 今後拡張する場合

初期版ではYAGNIを優先し、次は必要になるまで追加しません。

候補:

- 複数モデルのプリセット
- モデル別System Prompt
- ローカル音声認識
- Knowledge Base
- OCR
- 外部ストレージ
- チーム利用
- Redis / PostgreSQL / PGVector
- 独自UI
- MCP / Tools
- ローカルエージェント

ユーザーが実際に困った箇所から追加するのが推奨です。

---

# 26. 公式リファレンス

- Open WebUI Python environments  
  https://docs.openwebui.com/getting-started/quick-start/install-methods/python-environments/

- Open WebUI Environment Variables  
  https://docs.openwebui.com/reference/env-configuration/

- Open WebUI Hardening  
  https://docs.openwebui.com/getting-started/advanced-topics/hardening/

- Open WebUI Connection Errors / Reverse Proxy  
  https://docs.openwebui.com/troubleshooting/connection-error/

- Open WebUI License  
  https://github.com/open-webui/open-webui/blob/main/LICENSE

- Ollama FAQ  
  https://docs.ollama.com/faq

- Tailscale Serve  
  https://tailscale.com/docs/features/tailscale-serve

- Tailscale Serve CLI  
  https://tailscale.com/docs/reference/tailscale-cli/serve

- uv Locking environments  
  https://docs.astral.sh/uv/pip/compile/

- Apple Launch Agents  
  https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html

---

## 最後に

このプロジェクトの一番重要な設計方針は、**「既存環境を壊してまで自動化しない」**ことです。

ポートが使われていれば止まり、Ollamaが外部公開されていれば止まり、Tailscaleに既存経路があればそれを保持する。

GitHubで配布する場合も、「ワンコマンドで全部やる」ことより、利用者の既存Mac環境を守ることを優先してください。
