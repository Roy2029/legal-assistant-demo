
from enum import Enum
import re
import uuid
from pydantic import BaseModel
from typing import Optional,List
from .modules import Parser, log_module
from .data_model import StructuredDocument,ParagraphBlock,HeadingBlock,CodeBlock,TableBlock,ImageBlock


class SimpleTextParser(Parser):
    def _parse_impl(self, file_path: str) -> StructuredDocument:
        import uuid
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        return StructuredDocument(
            doc_id=str(uuid.uuid4()),
            blocks=[ParagraphBlock(content=content, order=0, type="paragraph")],
            source=file_path
        )
    

class TokenType(str, Enum):
    HEADING = "heading"
    TEXT = "text"
    CODE_FENCE = "code_fence"
    BLANK = "blank"

class MarkdownToken(BaseModel):
    type: "TokenType"

    content: str

    level: Optional[int] = None
    
class MarkdownParser(Parser):
    def tokenize(self, markdown: str) -> list[MarkdownToken]:

        tokens = []
        lines = markdown.splitlines()
        in_code_block = False

        for line in lines:
            stripped = line.strip()

            # 空行
            if not stripped:
                tokens.append(
                    MarkdownToken(
                        type=TokenType.BLANK,
                        content=""
                    ))
                continue

            # 代码块边界code fence（含开始和结束）
            if stripped.startswith("```"):
                tokens.append(
                    MarkdownToken(
                        type=TokenType.CODE_FENCE,
                        content=stripped
                    ))
                in_code_block = not in_code_block
                continue

            # code block 内部
            if in_code_block:
                tokens.append(
                    MarkdownToken(
                        type=TokenType.TEXT,
                        content=line
                    ))
                continue

            # 标题heading
            match = re.match(r"^(#{1,6})\s+(.*)", line)
            if match:
                level = len(match.group(1))
                content = match.group(2)
                tokens.append(
                    MarkdownToken(
                        type=TokenType.HEADING,
                        content=content,
                        level=level
                    ))
                continue

            # 普通文本
            tokens.append(
                MarkdownToken(
                    type=TokenType.TEXT,
                    content=line
                )
            )

        return tokens

    def parse_blocks(self, tokens: List[MarkdownToken]):

        blocks = []
        order = 0
        current_paragraph = []
        current_code = []
        in_code_block = False
        code_language = None

        def flush_paragraph():
            nonlocal current_paragraph
            nonlocal order

            if current_paragraph:
                content = "\n".join(current_paragraph)
                blocks.append(
                    ParagraphBlock(
                        order=order,
                        content=content
                    ))
                order += 1
                current_paragraph = []

        for token in tokens:

            # code fence
            if token.type == TokenType.CODE_FENCE:

                # 开始 code
                if not in_code_block:

                    flush_paragraph()

                    in_code_block = True

                    lang = token.content.replace("```", "").strip()

                    code_language = lang or None

                    current_code = []

                # 结束 code
                else:

                    blocks.append(
                        CodeBlock(
                            order=order,
                            content="\n".join(current_code),
                            language=code_language
                        ))
                    order += 1
                    current_code = []
                    in_code_block = False
                continue

            # code 内
            if in_code_block:
                current_code.append(token.content)
                continue

            # heading
            if token.type == TokenType.HEADING:

                flush_paragraph()#遇到开头标志就存储当前缓冲区的文本块

                blocks.append(
                    HeadingBlock(
                        order=order,
                        content=token.content,
                        level=token.level
                    )
                )

                order += 1

                continue

            # blank
            if token.type == TokenType.BLANK:

                flush_paragraph()

                continue

            # text
            if token.type == TokenType.TEXT:

                current_paragraph.append(token.content)

        flush_paragraph()

        return blocks
    
    def _parse_impl(self, file_path: str) -> StructuredDocument:

        with open(file_path, "r", encoding="utf-8") as f:
            markdown = f.read()

        tokens = self.tokenize(markdown)

        blocks = self.parse_blocks(tokens)

        return StructuredDocument(
            doc_id=str(uuid.uuid4()),
            source=file_path,
            blocks=blocks
        )
    
"""MarkdownParser
 ├── tokenize()
 ├── parse_blocks()
 └── build_document()"""