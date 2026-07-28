@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 试卷批改系统 —— 本地开发自测用（老师那边用的是 start.sh）
echo 起来后浏览器会自动打开 http://127.0.0.1:8899
echo 关掉这个黑窗口就是停止服务。
echo.
python -X utf8 app.py %*
echo.
echo 服务已停止。如果上面有报错，把报错内容发给 Claude。
pause
