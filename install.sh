#!/bin/bash
# 试卷批改系统 —— 第一次安装（在老师机器上跑一次就够了）
#
# 做三件事：检查 Python、准备 data 目录和密钥文件、在桌面放两个图标。
# 不联网、不装任何东西、不改系统设置。

set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1
ROOT="$(pwd)"

echo "=========================================================="
echo " 试卷批改系统 —— 安装"
echo " 程序目录：$ROOT"
echo "=========================================================="
echo ""

# 1) Python
PY=""
for c in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "[×] 没找到 Python 3。请先执行： sudo apt install -y python3"
    echo "    装完再跑一次本脚本。"
    read -r -p "按回车键关闭…" _
    exit 1
fi
echo "[√] Python：$($PY -V 2>&1)  （$(command -v "$PY")）"
echo "    架构：$(uname -m)"

# 2) 目录与密钥文件
mkdir -p data
chmod +x "$ROOT"/*.sh 2>/dev/null
if [ ! -f data/API_KEY.txt ]; then
    "$PY" -c "import sys; sys.path.insert(0, '.'); from server import config; print(config.ensure_key_file())" \
        >/dev/null 2>&1
fi
if [ -f data/API_KEY.txt ]; then
    echo "[√] 密钥文件：$ROOT/data/API_KEY.txt"
    echo "    （把智谱的 API Key 粘进去保存，AI 批改就能用；不填也不影响其它功能）"
fi

# 3) 桌面图标
DESK=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESK="$(xdg-user-dir DESKTOP 2>/dev/null)"
fi
[ -d "${DESK:-}" ] || DESK="$HOME/桌面"
[ -d "$DESK" ] || DESK="$HOME/Desktop"
[ -d "$DESK" ] || DESK="$HOME"

APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"

make_desktop() {
    # $1=文件名 $2=显示名 $3=命令 $4=说明 $5=图标
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$2
Comment=$4
Exec=bash "$3"
Path=$ROOT
Icon=$5
Terminal=true
Categories=Education;Office;
StartupNotify=false
EOF
    chmod +x "$1"
}

ICON="$ROOT/static/icon.svg"
[ -f "$ICON" ] || ICON="accessories-text-editor"

for target in "$DESK" "$APPS"; do
    [ -d "$target" ] || continue
    make_desktop "$target/试卷批改系统.desktop" "试卷批改系统" \
        "$ROOT/start.sh" "批改主观题、算成绩、出分析" "$ICON"
    make_desktop "$target/更新试卷批改系统.desktop" "更新试卷批改系统" \
        "$ROOT/update.sh" "更新到最新版（不会动你的成绩数据）" "$ICON"
done
echo "[√] 桌面图标已放到：$DESK"

# 某些桌面环境要求先「信任」才肯双击运行
if command -v gio >/dev/null 2>&1; then
    gio set "$DESK/试卷批改系统.desktop" metadata::trusted true 2>/dev/null
    gio set "$DESK/更新试卷批改系统.desktop" metadata::trusted true 2>/dev/null
fi

echo ""
echo "=========================================================="
echo " 装好了。双击桌面上的「试卷批改系统」就能用。"
echo ""
echo " 如果双击没反应，在图标上点右键 → 「允许运行」或「信任此文件」，"
echo " 再双击一次。"
echo "=========================================================="
echo ""
read -r -p "按回车键关闭…" _
