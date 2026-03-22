# 使用说明

这个项目当前只做两件事：

1. 打开浏览器，复用本地登录态。
2. 抓取网页中的 PDF 分片，并自动合并成完整 PDF。

不会再尝试修复文字编码，也不会输出正文文本。

## 1. 环境准备

在项目根目录执行：

```bash
npm install
python -m pip install pypdf
```

如果你本机 Chrome 不在默认位置，可以在运行时额外传入 `--browser-path`。

默认使用的浏览器路径是：

```text
C:\Users\ORR\AppData\Local\Google\Chrome\Application\chrome.exe
```

## 2. 最常用的命令

直接从目标书页开始：

```bash
npm run grab:pdf -- "https://你的书页地址"
```

或者：

```bash
node grab-pdf-cli.mjs --url "https://你的书页地址"
```

如果你想先打开空白页，再手动进入目标网站：

```bash
npm run grab:pdf --
```

如果页面不会自动暴露 PDF 密码，可以手动传入：

```bash
node grab-pdf-cli.mjs --url "https://你的书页地址" --pdf-password "PDF密码"
```

如果只想下载你选中的单个 PDF，不自动合并全部分片：

```bash
node grab-pdf-cli.mjs --url "https://你的书页地址" --single-segment
```

## 3. 手动操作流程

1. 运行命令后，脚本会打开浏览器。
2. 在浏览器里登录网站。
3. 打开目标书页。
4. 再进入真正显示 PDF 的阅读页。
5. 回到终端，按一次回车。
6. 如果检测到多个 PDF 候选，按提示输入编号。
7. 如果该书是分片 PDF，脚本会自动下载全部分片并合并。
8. 处理完成后，终端会打印输出目录。

## 4. 输出内容

默认输出目录是项目下的 `output/`。

对于分片书籍，通常会生成一个时间戳目录，例如：

```text
output/
  书籍标识-时间戳/
    segments/
    书籍标识-merged.pdf
    书籍标识-download-manifest.json
    书籍标识-processing.json
```

各文件含义：

- `segments/`：下载到的原始 PDF 分片。
- `*-merged.pdf`：自动拼接后的完整 PDF。
- `*-download-manifest.json`：下载来源、候选链接、分片列表等信息。
- `*-processing.json`：合并处理元数据，例如输入文件、页数映射、输出路径。

如果不是分片 PDF，则通常只会直接输出：

- `*.pdf`
- `*.json`

## 5. 常用参数

```text
--url <value>                  起始网页地址。
--output-dir <path>            输出目录，默认是项目下的 output。
--profile-dir <path>           浏览器用户目录，用来保留登录态。
--browser-path <path>          指定 Chrome/Chromium 路径。
--wait-after-login-ms <ms>     按回车后额外等待时间，默认 2500。
--max-attempts <n>             PDF 检测重试次数，默认 3。
--match <text>                 优先选择 URL 中包含该文本的 PDF 候选。
--timeout-ms <ms>              页面超时时间，默认 45000。
--pdf-password <value>         手动指定 PDF 密码。
--single-segment               只下载当前选中的单个 PDF，不自动合并全部分片。
--help, -h                     查看帮助。
```

## 6. 常见问题

### 6.1 浏览器打开了，但脚本一直找不到 PDF

先确认你已经进入真正的 PDF 阅读页，而不只是书籍详情页。

可以再做一次：

1. 在浏览器里点进阅读器。
2. 等页面完全加载。
3. 回终端按回车。

### 6.2 提示需要 PDF 密码

优先这样处理：

1. 先在浏览器里把 PDF 真正打开。
2. 再按回车，让脚本尝试自动检测密码。

如果还是失败，就手动传：

```bash
node grab-pdf-cli.mjs --url "https://你的书页地址" --pdf-password "PDF密码"
```

### 6.3 浏览器路径不对

手动指定浏览器：

```bash
node grab-pdf-cli.mjs --url "https://你的书页地址" --browser-path "D:\Path\To\chrome.exe"
```

### 6.4 想复用登录态，避免每次重新登录

默认就会使用项目下的 `.playwright-profile/`。

只要不要删除这个目录，通常下次运行时登录态还会保留。

## 7. 一个完整示例

```bash
node grab-pdf-cli.mjs --url "https://z.ipmph.com/zzfwh5/#/?en=...&rid=..."
```

运行后：

1. 在浏览器中登录并打开阅读器。
2. 回终端按回车。
3. 选择候选 PDF 编号。
4. 等待脚本下载分片并自动合并。

完成后直接在 `output/` 里打开 `*-merged.pdf` 即可。
