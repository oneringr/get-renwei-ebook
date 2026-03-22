# `repair_pdf_no_ocr.py` 使用说明

## 1. 适用场景

这个脚本用于修复这类 PDF：

- 页面主体本质上是整页图片
- PDF 里原本带有隐藏文字层
- 但隐藏文字层因为嵌入字体损坏、缺失或 `ToUnicode` 映射异常，导致：
  - 打开时报“无法提取嵌入的字体”
  - 复制文字乱码
  - 搜索不到正常中文

脚本**不使用 OCR**。它的原理是：

1. 从原 PDF 内容流中读取文字字节和坐标
2. 用对应字体的 `cmap` 反查 Unicode
3. 保留原页底图
4. 重新生成一层干净、可复制、可搜索的隐藏文本层

## 2. 不适用场景

下面几种情况，这个脚本不能保证恢复：

- PDF 只有图片，没有任何原始文字层
- 原始字码已经不可逆，且你也没有可用的原字体
- 没有 OCR 且没有人工转录文本时，无法“猜”出真实文字

## 3. 运行环境

建议使用 Python 3.10+。

脚本依赖：

- `PyMuPDF`
- `fonttools`
- `pypdf`

如果本机还没安装，可以运行：

```powershell
python -m pip install pymupdf fonttools pypdf
```

## 4. 准备字体目录

脚本需要一个字体目录，用来匹配 PDF 里引用的字体。

推荐做法：

1. 准备一个单独的字体目录，例如 `tmp_fonts_repo`
2. 把你收集到的 `.ttf` / `.ttc` / `.otf` 字体放进去
3. 方正类中文字体尽量放全

本脚本会同时扫描：

- 你传入的 `font_dir`
- `C:\Windows\Fonts`
- 当前工作目录

匹配顺序是：

1. PDF `BaseFont` 内部名精确匹配
2. 字体家族名匹配
3. 手工别名表匹配
4. `GB1/GBK` 通用回退匹配

## 5. 基本命令

命令格式：

```powershell
python repair_pdf_no_ocr.py input_pdf font_dir output_pdf
```

例如：

```powershell
python repair_pdf_no_ocr.py full.pdf tmp_fonts_repo full_repaired.pdf
```

## 6. 带人工覆盖文本的命令

如果某些页面的原始字码已经不可逆，但你手上有可信文本，可以通过 `--overrides` 指定人工覆盖内容。

命令格式：

```powershell
python repair_pdf_no_ocr.py input_pdf font_dir output_pdf --overrides overrides.json
```

例如：

```powershell
python repair_pdf_no_ocr.py full.pdf tmp_fonts_repo full_repaired.pdf --overrides repair_0_transcript.json
```

## 7. 参数说明

- `input_pdf`
  - 原始待修复 PDF
- `font_dir`
  - 你的字体目录
- `output_pdf`
  - 修复后的 PDF 输出路径
- `--overrides`
  - 可选
  - 指定一个 JSON 文件，用于覆盖部分页面的自动提取结果

查看帮助：

```powershell
python repair_pdf_no_ocr.py -h
```

## 8. 输出文件说明

每次运行后，通常会生成 3 个文件：

- `xxx.pdf`
  - 修复后的 PDF
- `xxx.font_mapping.json`
  - 字体匹配报告
  - 用来查看每个 PDF 字体最终匹配到了哪个本地字体
- `xxx.page_report.json`
  - 页面处理报告
  - 用来查看每页自动提取条数、合并条数、是否用了覆盖文本

## 9. 推荐操作流程

### 方案 A：完全自动

适合：

- 字体比较全
- 原始字码可逆

步骤：

1. 准备字体目录
2. 直接运行基本命令
3. 用 `pdffonts` / `pdftotext` 检查结果

### 方案 B：自动 + 局部人工覆盖

适合：

- 大部分页面能自动恢复
- 少数页面自动恢复不理想

步骤：

1. 先跑一遍自动修复
2. 找出问题页
3. 制作 `overrides.json`
4. 带 `--overrides` 再跑一遍

## 10. `overrides.json` 格式

格式是：

```json
{
  "1": [
    {
      "bbox": [100, 40, 320, 56],
      "text": "国家卫生健康委员会“十四五”规划教材",
      "fontsize": 12
    }
  ],
  "2": [
    {
      "bbox": [80, 120, 300, 136],
      "text": "儿童少年卫生学",
      "fontsize": 14
    }
  ]
}
```

说明：

- 键名是页码，从 `1` 开始
- `bbox` 格式是 `[x0, y0, x1, y1]`
- `text` 是该位置要写入的文本
- `fontsize` 可选，通常建议填写

如果某页出现在 `overrides.json` 里，那么这一页会优先使用你提供的文本，不再使用自动结果。

## 11. 如何验证修复结果

### 1. 检查字体

```powershell
pdffonts .\full_repaired.pdf
```

理想情况：

- 修复后的 PDF 不再依赖原来那批损坏字体
- 出现 `uni=yes`

### 2. 检查文本提取

```powershell
pdftotext .\full_repaired.pdf -
```

重点看：

- 中文是否还是乱码
- 关键标题是否可正常提取
- 是否还会出现整段重复

### 3. 检查视觉效果

直接用 Acrobat / Chrome / Edge 打开：

- 看是否还弹“无法提取嵌入字体”
- 看页面显示是否正常
- 看复制出来是否是正常中文

## 12. 常见问题

### Q1：运行后还是乱码

常见原因：

- 缺少对应原字体
- 字体虽然找到了，但不是同一套字形顺序
- 该页原始字码本身已经不可逆

建议：

1. 先看 `xxx.font_mapping.json`
2. 补充更接近的原字体
3. 对个别页改用 `--overrides`

### Q2：文本不是乱码了，但断句不自然

这是正常现象之一。

原因是：

- 脚本恢复的是原 PDF 隐藏层的文字粒度
- 不是重新理解段落后再排版

因此有时会出现：

- 半句换行
- 标题与正文之间断行比较机械

这不影响“可复制、可搜索、基本可读”。

### Q3：某些页和原 PDF 像素级不完全一致

脚本目标是：

- 保持视觉内容尽量不变
- 修复文字层

大多数页面会与原页完全一致；个别页面可能因为原图重嵌、颜色解码或页面对象结构差异，出现像素级微小变化，但不应影响正常阅读。

### Q4：修复后还是会弹字体报错吗

正常情况下不会。

因为修复后的 PDF 不再依赖原文件那批损坏的嵌入字体对象，而是使用新的隐藏 Unicode 文本层。

## 13. 当前版本的典型用法

### 修复 `0.pdf`

```powershell
python repair_pdf_no_ocr.py 0.pdf tmp_fonts_repo 0_repaired.pdf --overrides repair_0_transcript.json
```

### 修复 `full.pdf`

```powershell
python repair_pdf_no_ocr.py full.pdf tmp_fonts_repo full_repaired_generic.pdf --overrides repair_0_transcript.json
```

## 14. 一句话总结

最常用的手动流程就是：

1. 准备字体目录
2. 先自动跑一遍
3. 用 `pdftotext` 检查
4. 个别问题页再用 `--overrides` 修

