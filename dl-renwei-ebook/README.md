# 下载器模块

这个目录提供命令行下载器，用来：

- 启动并复用本地 Chrome/Chromium 登录态
- 侦测阅读页中的 PDF 请求
- 下载单个 PDF 或分片 PDF
- 自动合并分片产物
- 向根目录 GUI 输出结构化 JSON 事件

## 快速开始

安装依赖：

```powershell
npm install
```

最常用命令：

```powershell
npm run grab:pdf -- --url "https://你的书页地址"
```

或者：

```powershell
node grab-pdf-cli.mjs --url "https://你的书页地址"
```

更完整的参数说明、手动流程和常见问题见 `USAGE.md`。

## 说明

- 默认输出目录为当前目录下的 `output/`
- 默认浏览器登录配置目录为当前目录下的 `.playwright-profile/`
- 根目录 `ebook_gui.py` 会通过 `--gui-bridge` 模式与本模块通信

本模块随仓库一起按 GPL-3.0 发布，详见根目录 `LICENSE`。
