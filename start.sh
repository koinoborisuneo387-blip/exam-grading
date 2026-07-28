#!/bin/bash
# 试卷批改系统 —— 启动（银河麒麟 / 统信 UOS / 任何 Linux）
# 双击桌面上的「试卷批改系统」图标就会跑这个脚本。

cd "$(dirname "$(readlink -f "$0")")" || exit 1

# 找一个能用的 python3。国产系统自带的一般就叫 python3。
PY=""
for c in python3 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python; do
    if command -v "$c" >/dev/null 2>&1; then
        if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)' 2>/dev/null; then
            PY="$c"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo ""
    echo "=========================================================="
    echo " 没有找到 Python 3。"
    echo " 请在终端里执行下面这行装一下（会问你要开机密码）："
    echo "     sudo apt install -y python3"
    echo " 装完再双击一次这个图标。"
    echo "=========================================================="
    echo ""
    read -r -p "按回车键关闭…" _
    exit 1
fi

echo "用的是：$($PY -V 2>&1)"
"$PY" app.py "$@"
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo ""
    echo "=========================================================="
    echo " 程序意外退出了（退出码 $STATUS）。"
    echo " 请把上面这一整屏的文字拍照发给开发者。"
    echo "=========================================================="
    read -r -p "按回车键关闭…" _
fi
