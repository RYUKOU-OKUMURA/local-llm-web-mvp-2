# 確認した一次資料

確認日：2026-09-16。本パッケージは上流製品そのものを同梱・再実装していません。

| ID | 出典 | 実装へ反映した点 |
|---|---|---|
| S-01 | [Open WebUI / Python environments](https://docs.openwebui.com/getting-started/quick-start/install-methods/python-environments/) | 専用Python環境での公式パッケージ利用 |
| S-02 | [Open WebUI / Environment variables](https://docs.openwebui.com/reference/env-configuration/) | DATA_DIR、認証、永続化、ローカル埋め込み、外部機能停止、添付制限 |
| S-03 | [Open WebUI / PyPI](https://pypi.org/project/open-webui/) | 0.11.3、2026-08-31公開の配布記録を検索で確認。固定する初期候補 |
| S-04 | [Open WebUI / config.py](https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/config.py) | DEFAULT_CONFIGのキー構造。実機の採用版をAST解析して照合 |
| S-05 | [Open WebUI / configs.py](https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/routers/configs.py) | 管理者用GET export、POST import（configオブジェクト） |
| S-06 | [Open WebUI / auths.py](https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/routers/auths.py)・[main.py](https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/main.py)・[env.py](https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/env.py) | サインイン、role確認、onboarding/認証状態、Cookie設定 |
| S-07 | [Open WebUI / users.py](https://raw.githubusercontent.com/open-webui/open-webui/main/backend/open_webui/models/users.py) | user.roleの読み取り専用件数確認。空DBでの初期管理者再公開を避ける |
| S-08 | [Open WebUI / Updating](https://docs.openwebui.com/getting-started/updating/) | バックアップと更新の扱い |
| S-09 | [Ollama FAQ](https://docs.ollama.com/faq)・[macOS](https://docs.ollama.com/macos) | ネイティブ実行、loopback、OLLAMA_NO_CLOUD、資源制限 |
| S-10 | [Open WebUI / Hardening](https://docs.openwebui.com/getting-started/advanced-topics/hardening/) | コード実行、拡張依存、Functions等を使わない |
| S-11 | [RFC 6265 §8.5](https://www.rfc-editor.org/rfc/rfc6265#section-8.5) | Cookieはポートで分離されない |
| S-12 | [Tailscale Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve) | status JSON、--bg、専用HTTPSポート追加/削除、reset回避 |
| S-13 | [uv / Locking environments](https://docs.astral.sh/uv/pip/compile/) | 依存解決、ロック、実際の対象環境での同期 |
| S-14 | [Apple / Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) | ユーザーLaunchAgentによる起動管理 |
| S-15 | [Ollama qwen3:4b](https://ollama.com/library/qwen3:4b)・[bge-m3](https://ollama.com/library/bge-m3) | 試験用モデルタグの候補。性能・日本語品質の実機保証ではない |
| S-16 | [Ollama tags API](https://docs.ollama.com/api/tags)・[chat API](https://docs.ollama.com/api/chat)・[embed API](https://docs.ollama.com/api/embed)・[show API](https://docs.ollama.com/api/show) | ローカルモデル確認、応答、埋め込み、モデル情報 |

## 調査と検証の限界

PyPIには取得経路によって古い0.11.0を示すキャッシュもありました。検索結果の公開履歴では0.11.3（2026-08-31）とその配布ファイルを確認しました。実装の固定値は0.11.3ですが、「セキュリティ問題が一切ない版」「実機で検証済みの採用版」とは扱いません。

上流のmainソースは参照資料であり、0.11.3の固定タグのソースを完全に取得できたわけではありません。また、この実行環境からのパッケージ取得はネットワーク制約でできず、Open WebUI/Ollama本体の起動は未実施です。

この差を隠すために架空のmacOSロックや実機検証結果を同梱することはしません。実機のinstallでは、実際に取得した版・設定キー・Python版・ロックを記録し、必須設定を解釈できない場合は公開前に停止します。これは互換性の完全な証明ではなく、未知の状態で進めないための対策です。

Mac mini/Tailscaleのホスト名・秘密鍵・モデルダイジェストは、この配布ZIPで決め打ちしません。実機から取得またはローカル生成します。元仕様3文書の内容は`specs/`に保持しています。
