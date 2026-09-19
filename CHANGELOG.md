# Changelog

## 0.1.2 — 2026-09-19

ポート競合への対応。

- WebUI既定ポートを3000から3001へ変更（他のローカルWebツールとの競合を避けるため）。
- `install`・`restore-test`で指定ポートが使用中の場合、近い空きポートへ自動で一時割当する。割当結果は導入時に表示し、構成記録へ保存する。
- 自動割当の候補からOllama(11434)・OpenClaw(18789)を常時除外。
- `preflight`で使用中のポートに自動割当の候補番号を表示。
- `restore-test`の既定ポートを3010へ変更（新しいWebUI既定3001との重複を避けるため）。
- `setup.command`と各ドキュメントのポート表記を3001へ更新。

## 0.1.1 — 2026-09-16

実機preflightの結果を反映した安全性更新。

- WebUI用Tailscale Serveの既定HTTPSポートを8443から9443へ変更。
- `--ollama-mode existing`を追加。既存Ollamaを本パッケージの所有物として停止・再起動しない。
- 既存Ollama再利用時、`127.0.0.1:11434`以外の待受（wildcard / LAN / Tailscale公開）を拒否。
- `preflight`に`--web-port` / `--https-port`を追加し、Ollamaの待受エンドポイントとloopback判定を表示。
- BOSSのMac mini向け`setup.command`は9443＋既存Ollama再利用を初期値に変更。
- 既存8443番と443番のTailscale Serve経路は変更対象外。
- 新規安全テスト4件を追加。合計106件成功。

## 0.1.0 — 2026-09-16

初版。
