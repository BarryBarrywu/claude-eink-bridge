# Claude Code E-Ink HUD 墨水屏桌面看板

[English](./README_EN.md)

本项目是 [Claude HUD](https://github.com/jarrodwatts/claude-hud) 的硬件扩展桥接工具。它可以将你在终端使用 [Claude Code](https://docs.anthropic.com/zh-CN/docs/agents-and-tools/claude-code/overview) 时的实时状态（Token 消耗量、当前模型、上下文拥挤度等），无缝同步推送到你的 **Zectrix 墨水屏** 上，为你打造一个极客感满满的桌面 AI 物理看板。

![实拍效果图](device.jpg)

*(上图为实机运行效果。另外本项目目前仅支持 macOS 环境)*

## ✨ 功能特点

- **无痕伴随运行**：完全非侵入式设计，不修改官方原版代码。打开 Claude Code 时作为“影子组件”自动唤醒，关闭终端后 10 分钟自动销毁，平时完全释放后台资源。
- **极客级性能优化 (对 Mac 零负担)**：
  - **SSD 零磨损**：图片渲染全程在内存流中完成，不会在硬盘频繁生成乱七八糟的临时图片。
  - **强力防暴刷**：内建 30 秒磁盘冷却与 Hash 拦截机制。无论 AI 吐字多快，都不会引发 I/O 风暴，且只有数据改变时才发起网络请求（每次约 2KB 带宽）。
- **多开追踪支持**：同时开好几个终端跑 Claude Code？没关系，它会自动扫描追踪并展示最新活跃的那个项目。

---

## 📂 核心项目文件说明

如果你想研究或二次开发，这里是各个文件的用途：
- `install.sh` / `install.command`：为 Mac 用户准备的一键闪电安装脚本。
- `eink-wrapper.ts`：核心拦截器，负责“窃听”并截获 Claude 发送的内部状态。
- `setup-eink.mjs`：安装程序的底层配置脚本，负责把拦截器绑定到 Claude。
- `main.py`：Python 渲染主程序，负责把数据画成图片并推送到 Zectrix 墨水屏。
- `preview.png`：本说明文档顶部引用的演示图片。
- `font.ttf`：开源的 MiSans 字体文件，用于给 Python 渲染文字。
- `config.example.json`：默认的配置文件模板。

---

## 📊 面板信息与布局

![面板渲染原图](preview.png)

- **顶部状态栏**：可自定义的问候语（默认为“今天的Token用完了吗？”），以及数据最后更新的日期。
- **项目与模型信息**：当前正在使用的模型名称（如 `Claude 3.6 Sonnet`）、当前所在的代码项目文件夹名称，以及 Git 分支状态（如果有未提交的代码更改，会显示 `*` 星号）。
- **上下文健康度 (CONTEXT)**：直观的进度条显示当前对话上下文的拥挤程度，并附带精确的 Token 消耗量（比如 `12k / 200k`）。当上下文即将爆满时，会有超额预警。
- **API 额度使用率 (USAGE)**：分别展示 5 小时内和 7 天内的 API 额度使用百分比进度条。最实用的是，它还会显示额度重置的**倒计时**（例如 `2h15m`），让你精准掌握满血复活的时间。
- **底部状态栏**：显示本次 Session 对话已经持续的时间、是否有多个代码终端在同时运行，以及当前的时钟。

---

## 🛠️ 安装准备 (只需一次)

### 前置条件
在安装本工具前，请确保你的电脑已经安装了以下基础工具：
1. **[Claude Code](https://docs.anthropic.com/zh-CN/docs/agents-and-tools/claude-code/overview)**。
2. **[Claude HUD](https://github.com/jarrodwatts/claude-hud)**：原版的图形化面板工具，请先确保它能正常运行。
3. **[Node.js](https://nodejs.org/)** 和 **[Python 3](https://www.python.org/downloads/)**：运行脚本所需的基础环境。

### 一键安装 (推荐)
无需下载源码、解压、找文件夹。你只需要打开 Mac 的 **终端 (Terminal)**，复制下面这行命令粘贴进去，然后按回车：

```bash
curl -fsSL https://raw.githubusercontent.com/answer24/claude-eink-bridge/main/install.sh | bash
```

**这个命令会自动在后台帮你做好一切：**
1. 自动下载代码到目录 `~/.claude-eink-bridge`。
2. 自动配置拦截器和依赖环境。
3. 自动生成一个默认的配置文件。

当终端里弹出 `🎉 闪电安装全部完成！` 的提示时，**系统会自动弹出一个文本编辑窗口**打开 `config.json` 文件（如果没有自动弹出，你也可以手动运行 `open ~/.claude-eink-bridge/config.json` 或者是用 VSCode 打开它）。

---

## ⚙️ 绑定墨水屏 (Zectrix 平台配置)

接下来的操作都需要在网页后台进行获取，请先在浏览器打开并登录 Zectrix 云平台：**https://cloud.zectrix.com/**

在 `config.json` 里，你需要填写以下三个关键参数：`api_key`、`mac_address` 和 `page_id`。

### 1. 获取 API Key (`api_key`)
- 在 Zectrix 云平台左侧点击 **开放API** 。
- 点击“创建API Key”，将生成的那串代码复制并替换掉 `config.json` 里的 `"YOUR_ZECTRIX_API_KEY"`。

### 2. 获取设备 MAC 地址 (`mac_address`)
- 在 Zectrix 云平台左侧点击 **设备管理**，找到你的墨水屏，复制它的 MAC 地址并填入 `config.json`。

### 3. 设置页面 ID (`page_id`)
墨水屏可以有多个页面，由于推送的内容会直接覆盖掉原有的页面，你需要告诉程序推送到第几页：
- 配置文件中默认 `page_id` 填的是 `5`，也就是说它会自动覆盖推送到你的墨水屏的第 5 页。
- 如果你的屏幕平时只用前 3 页，或者你想让它推送到其他页面（比如第 1 页主页），只需要将 `config.json` 里的 `page_id` 修改为你想要的数字即可（注意：填数字，不要加双引号）。

**最终你的 `config.json` 应该长这样：**
```json
{
  "api_key": "sec_1234567890abcdef",
  "mac_address": "A1:B2:C3:D4:E5:F6",
  "page_id": 5,
  "interval_seconds": 60,
  "greeting": "今天的Token用完了吗？",
  "font_path": "font.ttf"
}
```

> **💡 小贴士：** 为了获得最佳的看板体验，建议在 Zectrix 后台将设备的“轮询时间”设置为 **1分钟**，同时保持 `config.json` 里的 `interval_seconds` 为 **60**。

---

## 🚀 开始使用

在终端里输入 `claude` 正常使用你的 Claude Code。几秒钟后，你的墨水屏就会自动刷新出炫酷的实时面板！

当你结束工作，关闭所有 Claude Code 进程后 10 分钟，后台进程也会自动安静退出，不留痕迹。

---

## ❓ 常见问题排查 (FAQ)

**Q: 我填好了配置，为什么屏幕一直不刷新？**
A: 可以尝试手动排查。打开 Mac 的终端 (Terminal)，输入以下命令：
```bash
cd ~/.claude-eink-bridge
source .venv/bin/activate
python main.py --preview
```
这会在文件夹里生成一张 `preview-local.png` 图片，如果没有生成或者报错，说明配置填写有误或网络不通。如果生成了但屏幕没变，说明是 Zectrix API Key 或 Mac 地址填错了。

**Q: 更新了项目代码后，改动没有生效？**
A: 这是正常现象。Claude Code 实际运行的是安装时复制到 `~/.claude/eink-wrapper.ts` 的文件，而不是项目目录里的源文件。每次拉取新代码后，需要重新运行一次安装命令让改动生效：
```bash
curl -fsSL https://raw.githubusercontent.com/answer24/claude-eink-bridge/main/install.sh | bash
```
安装脚本会跳过已有的 venv 和 config，只更新拦截器文件，不会影响你的配置。

**Q: 我修改了配置（比如改了问候语、换了字体），怎么重启服务让它生效？**
A: 因为程序为了做到完全无感，是一直在后台静默运行的，它不会实时监听配置变化。你需要先强制关掉它。打开终端运行：
```bash
pkill -f main.py
```
运行完毕后没有任何提示是正常的。接着你只需在任意终端重新输入 `claude` 唤醒 Agent，它就会自动拉起全新的后台服务，你的新配置也就生效了！

**Q: 怎么卸载这个工具，恢复到原版状态？**
A: 打开终端，运行以下命令，即可解除对 Claude Code 的绑定：
```bash
node ~/.claude-eink-bridge/setup-eink.mjs --undo
```

**Q: 推送过去的不是图片吗？为什么还需要配置字体文件？**
A: 是的，最终推送到墨水屏的确实是一张图片。但这块“画板”是在你的电脑本地实时渲染生成的。程序在把 Claude 消耗的“文字数据”转化为“图片”时，必须要依赖 `font.ttf` 字体文件作为画笔，才能知道如何绘制文字。
本项目默认已经为你内置了 **小米的 MiSans (Medium)** 字体，它在墨水屏上显示极为清晰，且属于全社会免费商用的开源字体，你可以放心使用。

**Q: 我想自定义顶部的问候语？**
A: 没问题！只需打开 `~/.claude-eink-bridge/config.json`，更改 `greeting` 后面的文字即可（比如 `"greeting": "Code, Eat, Sleep"`）。注意：为了排版美观，建议不要超过 12 个中文字符或 25 个英文字母，超出的部分会被自动截断显示为 `...`。

**Q: 我想换个别的字体？**
A: 只要把你想用的中文字体（`.ttf` 格式）放到 `~/.claude-eink-bridge` 文件夹里，重命名为 `font.ttf`，覆盖现有的文件即可（或者修改 `config.json` 里的 `font_path` 绝对路径）。比如换成你喜欢的复古像素字体！

---

## 📺 关注我们

如果这个小工具帮助到了你，或者让你的桌面变得更酷了，**欢迎来 B 站关注我们！**

- 🔲 **[极趣实验室 (硬件官方)](https://space.bilibili.com/13131424)**：这块超酷的 Zectrix 墨水屏就是出自他们之手！关注获取更多硬核桌搭硬件。
- 👨‍💻 **[最近使用 (本项目作者)](https://space.bilibili.com/217963572)**：欢迎订阅我的频道，一起折腾更多有趣的苹果生态与 AI 效率工具！

