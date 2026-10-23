#!/usr/bin/env bash
# Ubuntu desktop launcher; all environment changes apply only to this process.
set -euo pipefail

launcher_path="$(readlink -f -- "${BASH_SOURCE[0]}")"
launcher_root="$(dirname -- "$launcher_path")"
launcher_python="$launcher_root/.conda/bin/python"

if [[ ! -x "$launcher_python" ]]; then
    printf '%s\n' '未找到项目 Conda 环境（.conda/bin/python），请先按 README 完成环境搭建。' >&2
    exit 1
fi

launcher_config="$launcher_root/config/local.toml"
if [[ ! -f "$launcher_config" ]]; then
    launcher_config="$launcher_root/config/default.toml"
fi

launcher_env=(env -u PYTHONPATH "QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-xcb}")
launcher_qt_libs="$launcher_root/.cache/qt-xcb/root/usr/lib/x86_64-linux-gnu"
if [[ -f "$launcher_qt_libs/libxcb-cursor.so.0" ]]; then
    launcher_env+=("LD_LIBRARY_PATH=$launcher_qt_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}")
fi

# Keep model/config/output paths independent of the caller's working directory.
cd -- "$launcher_root"
exec "${launcher_env[@]}" "$launcher_python" -m livetranslate live \
    --config "$launcher_config" "$@"
