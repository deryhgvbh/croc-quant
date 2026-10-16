@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
    echo 首次运行：创建虚拟环境并安装依赖（约3-5分钟）...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    call .venv\Scripts\activate.bat
)
echo 启动鳄鱼派量化看板，浏览器将自动打开 http://localhost:8501
streamlit run app.py
pause
