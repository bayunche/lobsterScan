"""上传附件的文本抽取模块。

material agent 拿到的只能是文本(LLM 看不了二进制)。这里把常见办公文档抽成纯文本
拼进 raw_text,让 agent 真正能读到内容。

支持矩阵:
- text/markdown / text/plain / application/json → 直接读
- .docx (python-docx)          → 段落 + 表格
- .xlsx (openpyxl)             → 多 sheet 转 markdown 表
- application/pdf (pdfminer)   → 全文
- 图片 / 音视频 / 其它二进制   → 不抽,标记 "需复制核心内容到文本框"
"""

from .extractor import extract_attachment_text, ExtractResult

__all__ = ["extract_attachment_text", "ExtractResult"]
