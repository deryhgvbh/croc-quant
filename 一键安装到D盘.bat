@echo off
chcp 65001 >nul
title 鳄鱼派量化 - 一键安装到 D:\croc-quant

echo ============================================
echo   鳄鱼派量化看板 - 本地安装
echo   目标位置: D:\croc-quant
echo ============================================
echo.

if not exist D:\croc-quant mkdir D:\croc-quant
cd /d D:\croc-quant

echo [1/4] 下载代码...
powershell -NoProfile -Command "Invoke-WebRequest 'https://codeload.github.com/deryhgvbh/croc-quant/zip/refs/heads/main' -OutFile 'repo.zip'"
if errorlevel 1 (
    echo 下载失败，请检查网络后重试。
    pause
    exit /b 1
)

echo [2/4] 解压...
powershell -NoProfile -Command "Expand-Archive -Force repo.zip ."
xcopy /E /Y /H /Q croc-quant-main\* . >nul
rmdir /s /q croc-quant-main
del repo.zip

echo [3/4] 创建虚拟环境并安装依赖（首次约3-5分钟）...
if not exist .venv (
    python -m venv .venv 2>nul || py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo 依赖安装失败。请确认已安装 Python 3.10+ 并勾选 Add to PATH。
    pause
    exit /b 1
)

echo.
echo [4/4] 启动看板，浏览器将打开 http://localhost:8501
echo      以后每天用：双击 D:\croc-quant\启动看板.bat
echo.
python -m streamlit run app.py
pause
