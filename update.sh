#!/bin/bash
# 试卷批改系统 —— 更新程序
#
# 铁律：**只换程序文件，data 目录一个字节都不碰。**
# data 里是老师的成绩、答卷原图、批注和 API Key，弄丢了找不回来。
#
# 有 git 就 git pull；没装 git 就从 GitHub 下 zip 覆盖。两条路都不动 data/。

set -u

# ---- 代码仓地址（换仓库改这一行就行）----------------------------------
REPO_SLUG="koinoborisuneo387-blip/exam-grading"
BRANCH="main"
# ----------------------------------------------------------------------

cd "$(dirname "$(readlink -f "$0")")" || exit 1
ROOT="$(pwd)"

echo "=========================================================="
echo " 试卷批改系统 —— 更新"
echo " 程序目录：$ROOT"
if [ -f VERSION ]; then echo " 当前版本：$(cat VERSION)"; fi
echo "=========================================================="
echo ""

if [ ! -f app.py ] || [ ! -d server ]; then
    echo "这个目录看起来不是试卷批改系统的程序目录，没敢动。"
    read -r -p "按回车键关闭…" _
    exit 1
fi

# 先把当前代码备份进 data/_backup/，出问题能退回去
BACKUP_DIR="data/_backup/$(cat VERSION 2>/dev/null || echo unknown)_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR" 2>/dev/null
for item in app.py server static VERSION start.sh update.sh install.sh; do
    [ -e "$item" ] && cp -r "$item" "$BACKUP_DIR/" 2>/dev/null
done
echo "旧版本已备份到：$BACKUP_DIR"
echo ""

updated=0

if [ -d .git ] && command -v git >/dev/null 2>&1; then
    echo "用 git 更新…"
    if git -c core.fileMode=false pull --ff-only origin "$BRANCH"; then
        updated=1
    else
        echo ""
        echo "git 更新没成功（可能是本地文件被改过，或者连不上网）。"
        echo "改用下载压缩包的方式再试一次…"
        echo ""
    fi
fi

if [ "$updated" -eq 0 ]; then
    ZIP_URL="https://codeload.github.com/${REPO_SLUG}/zip/refs/heads/${BRANCH}"
    TMP="$(mktemp -d)"
    echo "从网上下载最新版…"
    ok=0
    if command -v curl >/dev/null 2>&1; then
        curl -fL --connect-timeout 20 -o "$TMP/new.zip" "$ZIP_URL" && ok=1
    elif command -v wget >/dev/null 2>&1; then
        wget -q --timeout=20 -O "$TMP/new.zip" "$ZIP_URL" && ok=1
    else
        echo "这台电脑上没有 curl 也没有 wget，下载不了。"
    fi

    if [ "$ok" -ne 1 ]; then
        echo ""
        echo "下载失败。常见原因：没联网，或者单位网络不让访问 GitHub。"
        echo "解决办法：让开发者把新版压缩包发给你，手动解压覆盖（同样不要动 data 文件夹）。"
        rm -rf "$TMP"
        read -r -p "按回车键关闭…" _
        exit 1
    fi

    if command -v unzip >/dev/null 2>&1; then
        unzip -q -o "$TMP/new.zip" -d "$TMP/x"
    else
        python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
            "$TMP/new.zip" "$TMP/x" || {
            echo "解压失败。"; rm -rf "$TMP"; read -r -p "按回车键关闭…" _; exit 1; }
    fi

    SRC="$(find "$TMP/x" -maxdepth 1 -mindepth 1 -type d | head -n 1)"
    if [ -z "$SRC" ] || [ ! -f "$SRC/app.py" ]; then
        echo "下载到的压缩包内容不对，没敢覆盖。"
        rm -rf "$TMP"
        read -r -p "按回车键关闭…" _
        exit 1
    fi

    # 只覆盖程序文件。data/ 和 API_KEY.txt 一律跳过。
    rm -rf server static
    for item in app.py server static VERSION start.sh update.sh install.sh \
                README.md 使用说明.md SPEC.md CLAUDE.md AGENTS.md 试卷批改系统.desktop; do
        [ -e "$SRC/$item" ] && cp -r "$SRC/$item" "$ROOT/"
    done
    rm -rf "$TMP"
    updated=1
fi

chmod +x "$ROOT"/*.sh 2>/dev/null

echo ""
if [ "$updated" -eq 1 ]; then
    echo "=========================================================="
    echo " 更新完成。新版本：$(cat VERSION 2>/dev/null || echo 未知)"
    echo ""
    echo " 接下来：关掉正在运行的程序窗口，重新双击「试卷批改系统」，"
    echo " 打开网页后看右上角的版本号是不是变成了上面这个。"
    echo "=========================================================="
else
    echo "没有更新成功，程序还是原来的版本。"
fi
echo ""
read -r -p "按回车键关闭…" _
