#!/usr/bin/env bash
# Claude E-Ink Bridge - 一键安装脚本 (Mac)

# 确保脚本在当前目录执行
cd "$(dirname "$0")"

echo "======================================"
echo "  Claude E-Ink Bridge 安装向导"
echo "======================================"
echo ""

# 1. 检查 Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到 Python 3。请先安装 Python 3 (https://www.python.org/downloads/)"
    exit 1
fi

# 2. 检查 Node / Bun
if ! command -v bun &> /dev/null && ! command -v node &> /dev/null; then
    echo "❌ 错误: 需要安装 Node.js 或 Bun。推荐安装 Node.js (https://nodejs.org/)"
    exit 1
fi

# 3. 复制 eink-wrapper.ts 到 ~/.claude 目录
echo "⏳ 正在配置 Claude HUD 拦截器..."
mkdir -p ~/.claude
cp eink-wrapper.ts ~/.claude/eink-wrapper.ts
if [ $? -ne 0 ]; then
    echo "❌ 错误: 复制 eink-wrapper.ts 失败"
    exit 1
fi

# 4. 执行 setup-eink.mjs 进行设置
echo "⏳ 正在配置自动启动环境..."
if command -v node &> /dev/null; then
    node setup-eink.mjs
else
    bun run setup-eink.mjs
fi

# 5. 创建 Python 虚拟环境
echo "⏳ 正在设置 Python 虚拟环境并安装依赖 (这可能需要几分钟)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "❌ 错误: 安装 Python 依赖失败"
    exit 1
fi

# 6. 生成 config.json
if [ ! -f config.json ]; then
    cp config.example.json config.json
    echo "✅ 已生成默认配置文件 config.json"
fi

echo ""
echo "======================================"
echo "🎉 安装成功！"
echo "下一步："
echo "1. 请用文本编辑器打开本目录下的 config.json"
echo "2. 填入你的 Zectrix API Key、Mac 地址和 Page ID"
echo "3. 配置好后，下次启动 Claude Code 时，屏幕就会自动更新了！"
echo "======================================"
