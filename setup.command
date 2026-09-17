#!/bin/bash
# Menu intentionally keeps administrator creation and exposure as separate steps.
set -u
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf '\n個人用ローカルLLM Web環境 — 導入メニュー\n'
printf '必ずサーバーにするMac miniで実行してください。MacBook側の導入は不要です。\n'
printf '既存のOpenClaw・443番・Dockerは変更対象にしません。\n'
while true; do
  printf '\n0 現状確認（読み取りだけ）\n1 専用Python / WebUIの導入\n2 Ollamaを起動\n3 試験用モデルを取得\n4 WebUIを起動（初回はMac mini内だけ）\n5 管理者作成後、安全設定を反映\n6 Tailscaleへ専用HTTPS経路を追加\n7 診断\n8 バックアップ\nq 終了\n'
  read -r -p '番号: ' choice || exit 0
  case "$choice" in
    0) args=(preflight --https-port 9443) ;;
    1) args=(install --https-port 9443 --ollama-mode existing) ;;
    2) args=(start ollama) ;;
    3) args=(pull-models) ;;
    4) args=(start webui) ;;
    5) args=(secure) ;;
    6) args=(publish) ;;
    7) args=(doctor) ;;
    8) args=(backup) ;;
    q|Q) exit 0 ;;
    *) printf '表示された番号を選んでください。\n'; continue ;;
  esac
  if /bin/bash "$HERE/llmweb" "${args[@]}"; then
    if [ "$choice" = 4 ]; then
      printf '初回はMac miniのブラウザで http://127.0.0.1:3000 を開き、管理者を作成してください。\n'
      printf 'ポートを変更している場合は変更先を使います。HTTPS設定済みなら正規HTTPS URLを使います。\n'
    fi
  else
    printf '\nこの工程は完了していません。エラーを解消するまで次の番号へ進まないでください。\n'
  fi
done
