#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "首次运行：创建虚拟环境并安装依赖（约3-5分钟）..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
else
    source .venv/bin/activate
fi
echo "启动鳄鱼派量化看板，浏览器打开 http://localhost:8501"
streamlit run app.py
