#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname -- "$script_dir")

if [ "${TALENT_RADAR_HOST+x}" != "x" ]; then
  TALENT_RADAR_HOST="127.0.0.1"
  export TALENT_RADAR_HOST
fi
if [ "${TALENT_RADAR_PORT+x}" != "x" ]; then
  TALENT_RADAR_PORT="8765"
  export TALENT_RADAR_PORT
fi
if [ "${TALENT_RADAR_DB+x}" != "x" ]; then
  TALENT_RADAR_DB="$project_dir/data/talent_radar.db"
  export TALENT_RADAR_DB
fi

python_command=${TALENT_RADAR_PYTHON:-python3}
cd "$project_dir"
exec "$python_command" app.py
