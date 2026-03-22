# get-renwei-ebook

一个面向本地桌面使用的电子书下载器，包含以下组件：

- `dl-renwei-ebook/`：复用浏览器登录态，抓取并合并人卫电子书 PDF。
- `pdf_fixer/`：在不使用 OCR 的前提下，重建隐藏文字层，修复可复制/可搜索文本。
- `ebook_gui.py`：统一的 `tkinter` 图形界面，串联“下载 -> 检测 -> 按需修复 -> 重命名 -> 导出清理”流程。

## 目录结构

```text
.
├─ ebook_gui.py
├─ requirements.txt
├─ dl-renwei-ebook/
│  ├─ grab-pdf-cli.mjs
│  ├─ grab-pdf-lib.mjs
│  └─ USAGE.md
└─ pdf_fixer/
   ├─ repair_pdf_no_ocr.py
   └─ repair_pdf_no_ocr_usage.md
```

## 功能概览

- 启动本机 Chrome/Chromium，复用本地登录态。
- 自动检测 PDF 候选，支持分片下载与合并。
- 下载完成后抽样检查文字层乱码。
- 发现疑似乱码时自动调用修复脚本。
- 从标题页提取书名与版次，整理最终文件名。
- 支持把最终 PDF 导出到指定目录，并清理本次运行生成的中间文件。

## 环境要求

- Windows 10/11
- Python 3.10+
- Node.js 18+
- 已安装本机 Chrome 或兼容的 Chromium

`tkinter` 为标准库组件；在 Windows 官方 Python 安装中通常默认可用。

## 安装

先安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

再安装下载器依赖：

```powershell
cd dl-renwei-ebook
npm install
```

如果你需要使用自动修复，请自行准备本地字体目录，并在 GUI 中指向该目录。仓库默认不包含字体资源，可以在`[font下载](https://github.com/zyh1102/fonts)`处下载。

## 快速开始

启动图形界面：

```powershell
python ebook_gui.py
```

GUI 主流程：

1. 启动浏览器并进入目标阅读页。
2. GUI 驱动下载器检测并抓取 PDF。
3. 下载后自动检查关键页文字层是否疑似乱码。
4. 需要时自动调用修复器。
5. 自动整理最终文件名。
6. 可一键导出最终 PDF，并清理本次运行产物。

如果你只想使用命令行下载器，请参考 `dl-renwei-ebook/USAGE.md`。

如果你只想单独使用修复器，请参考 `pdf_fixer/repair_pdf_no_ocr_usage.md`。

## 合规提醒

本项目仅适用于你对目标资源拥有合法访问和备份权限的场景。请在使用前自行确认相关平台条款、版权与授权要求。

## 许可证

本仓库采用 GNU General Public License v3.0，详见 `LICENSE`。
