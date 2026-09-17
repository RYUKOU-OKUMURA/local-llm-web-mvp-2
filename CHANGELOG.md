# Changelog

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
