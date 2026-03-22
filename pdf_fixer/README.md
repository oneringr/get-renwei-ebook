# PDF 修复器

`repair_pdf_no_ocr.py` 用于修复“页面是图片，但隐藏文字层可逆”的 PDF。

它不会做 OCR，而是尽量利用原 PDF 中已有的文字字节、字体映射和坐标信息，重新生成一层可复制、可搜索的隐藏文本层。

## 适用场景

- 原 PDF 打开正常，但复制文字乱码。
- 搜索不到中文或关键标题。
- 阅读器提示嵌入字体损坏或无法提取。

## 快速使用

```powershell
python repair_pdf_no_ocr.py input.pdf path\\to\\fonts output.pdf
```

完整参数、覆盖文本格式和验证方法见 `repair_pdf_no_ocr_usage.md`。

## 依赖

- `PyMuPDF`
- `fonttools`
- `pypdf`

可在仓库根目录运行：

```powershell
python -m pip install -r ..\\requirements.txt
```

## 字体说明

仓库默认不附带字体库。请在本机准备一个合法可用的字体目录，再通过命令行参数或 GUI 指向该目录。
