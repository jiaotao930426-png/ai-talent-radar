#!/usr/bin/env sh
set -eu

case "$(uname -s)" in
  Darwin) ;;
  *)
    printf '%s\n' "此脚本只适用于 macOS。"
    exit 1
    ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname -- "$script_dir")
template="$project_dir/launchd/com.qft.ai-talent-radar.plist.template"
user_home_dir=${HOME:?无法确定当前用户的主目录}
launch_agents_dir="$user_home_dir/Library/LaunchAgents"
label="com.qft.ai-talent-radar"
target="$launch_agents_dir/$label.plist"

if [ ! -f "$template" ]; then
  printf '%s\n' "找不到 launchd 模板：$template" >&2
  exit 1
fi

python_command=${TALENT_RADAR_PYTHON:-}
if [ -z "$python_command" ]; then
  for candidate in python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      python_command=$(command -v "$candidate")
      break
    fi
  done
fi
if [ -z "$python_command" ]; then
  printf '%s\n' "未找到 Python 3.11。请先安装 Python，或设置 TALENT_RADAR_PYTHON。" >&2
  exit 1
fi

escape_sed() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

project_value=$(escape_sed "$project_dir")
python_value=$(escape_sed "$python_command")
mkdir -p "$launch_agents_dir" "$project_dir/data"
sed \
  -e "s|__PROJECT_DIR__|$project_value|g" \
  -e "s|__PYTHON_BIN__|$python_value|g" \
  "$template" > "$target"

user_id=$(id -u)
launchctl bootout "gui/$user_id" "$target" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$user_id" "$target"
launchctl kickstart -k "gui/$user_id/$label"
printf '%s\n' "已安装并启动 macOS 自动启动任务：$label"
printf '%s\n' "访问地址：http://127.0.0.1:8765/"
