#!/usr/bin/env bash
set -e

echo "======================================"
echo "  Claude E-Ink Bridge 闪电安装向导"
echo "======================================"
echo ""

GITHUB_USERNAME="BarryBarrywu"
REPO_URL="https://github.com/${GITHUB_USERNAME}/claude-eink-bridge.git"
INSTALL_DIR="$HOME/.claude-eink-bridge"

# 1. 检查基础环境
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未找到 Python 3。请先安装: https://www.python.org/downloads/"
    exit 1
fi

if ! command -v bun &> /dev/null && ! command -v node &> /dev/null; then
    echo "❌ 错误: 需要安装 Node.js 或 Bun。推荐 Node.js: https://nodejs.org/"
    exit 1
fi

if ! command -v git &> /dev/null; then
    echo "❌ 错误: 未找到 git，请先安装 git。"
    exit 1
fi

# 2. 下载或更新代码
if [ -d "$INSTALL_DIR" ]; then
    echo "⏳ 发现已安装过，正在拉取最新代码..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    echo "⏳ 正在从 GitHub 下载代码库..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 3. 部署拦截器
echo "⏳ 正在配置 Claude HUD 拦截器..."
mkdir -p ~/.claude
cp eink-wrapper.ts ~/.claude/eink-wrapper.ts
if command -v node &> /dev/null; then
    node setup-eink.mjs
else
    bun run setup-eink.mjs
fi

# 4. 配置 Python 环境
echo "⏳ 正在设置 Python 虚拟环境并安装依赖 (需要稍等片刻)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt --quiet

# 5. 生成配置文件
CONFIG_CREATED=false
if [ ! -f "$INSTALL_DIR/config.json" ]; then
    echo "⏳ 正在生成默认配置文件..."
    cp config.example.json "$INSTALL_DIR/config.json"
    CONFIG_CREATED=true
fi

echo ""
echo "======================================"
echo "🎉 闪电安装全部完成！"
echo "👉 请编辑 ~/.claude-eink-bridge/config.json 文件"
echo "👉 填入你的 API Key、Mac 地址和 Page ID。"
echo "👉 填完直接保存，下次打开 Claude Code 时，屏幕就会自动更新！"
echo "======================================"

# 6. 自动打开配置文件（仅首次安装，更新时跳过）
if [ "$CONFIG_CREATED" = true ] && command -v open &> /dev/null; then
    echo "💡 正在为你自动打开配置文件..."
    sleep 1
    open "$INSTALL_DIR/config.json"
fi
