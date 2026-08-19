#!/usr/bin/env sh
set -eu

case "$(uname -s)" in
  Darwin) ;;
  *)
    printf '%s\n' "此脚本只适用于 macOS。"
    exit 1
    ;;
esac

user_home_dir=${HOME:?无法确定当前用户的主目录}
label="com.qft.ai-talent-radar"
target="$user_home_dir/Library/LaunchAgents/$label.plist"
user_id=$(id -u)

launchctl bootout "gui/$user_id" "$target" >/dev/null 2>&1 || true
if [ -f "$target" ]; then
  rm -f "$target"
fi
printf '%s\n' "已停止并移除 macOS 自动启动任务：$label"
