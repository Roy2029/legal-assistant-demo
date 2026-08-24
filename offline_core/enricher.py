from .modules import MetadataEnricher, Chunk
from pathlib import Path
import re
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class MetadataPipeline:
    def __init__(self, enrichers):
        self.enrichers = enrichers

    def run(self, chunks, document):
        enriched = []
        for chunk in chunks:
            for enricher in self.enrichers:
                chunk = enricher.enrich(
                    chunk,
                    document
                )
            enriched.append(chunk)
        return enriched

class SourceMetadataEnricher(MetadataEnricher):
    def enrich(self, chunk, document):
        path = Path(document.source)
        chunk.metadata.update({
            "source": document.source,
            "file_name": path.name,
            "file_type": path.suffix.replace(".", "")
        })
        return chunk
    
class StructureMetadataEnricher(MetadataEnricher):
    def enrich(self, chunk, document):
        chunk.metadata.update({
            "heading_path": chunk.heading_path,
            "section_depth": len(chunk.heading_path),
            "block_count": len(chunk.block_ids)
        })
        return chunk
    
import re
from collections import Counter


class KeywordEnricher(MetadataEnricher):

    def __init__(self, top_k=5):
        self.top_k = top_k

    def enrich(self, chunk, document):
        words = re.findall(r"\w+", chunk.text.lower())
        counter = Counter(words)
        keywords = [
            word
            for word, _ in counter.most_common(self.top_k)
        ]
        chunk.metadata["keywords"] = keywords

        return chunk
    
class LanguageEnricher(MetadataEnricher):

    def enrich(self, chunk, document):
        text = chunk.text
        if any('\u4e00' <= c <= '\u9fff' for c in text):
            lang = "zh"
        else:
            lang = "en"
        chunk.metadata["language"] = lang
        return chunk


def _extract_bbbs(file_path: str) -> str | None:
    """\u4ece\u6587\u4ef6\u540d\u63d0\u53d6 bbbs \u6807\u8bc6\u7b26\u3002

    \u6587\u4ef6\u540d\u683c\u5f0f: {bbbs}_{title}.docx
    bbbs \u4e3a\u56fa\u5b9a 32 \u4f4d\u6570\u5b57\u524d\u7f00\u3002
    """
    match = re.match(r'^(\d+)_', Path(file_path).stem)
    return match.group(1) if match else None


class CsvMetadataEnricher(MetadataEnricher):
    """\u4ece\u5143\u6570\u636e CSV \u8868\u8bfb\u53d6\u7ed3\u6784\u5316\u5b57\u6bb5\uff0c\u6ce8\u5165 Chunk metadata\u3002

    \u901a\u8fc7\u6587\u4ef6\u540d\u4e2d\u7684 bbbs \u524d\u7f00\u5efa\u7acb\u6587\u4ef6\u672c\u4f53\u4e0e\u5143\u6570\u636e\u884c\u7684\u6620\u5c04\u3002
    CSV \u9700\u5305\u542b bbbs \u5217\u4f5c\u4e3a\u552f\u4e00\u6807\u8bc6\u3002

    \u7528\u6cd5:
        enricher = CsvMetadataEnricher("path/to/metadata.csv")
        enricher.enrich(chunk, document)
    """

    def __init__(self, csv_path: str, bbbs_column: str = "bbbs"):
        """
        Args:
            csv_path: CSV \u6587\u4ef6\u8def\u5f84\uff08\u652f\u6301\u76f8\u5bf9\u8def\u5f84\u548c\u7edd\u5bf9\u8def\u5f84\uff09
            bbbs_column: \u6807\u8bc6\u5217\u540d\uff0c\u9ed8\u8ba4 "bbbs"
        """
        self.csv_path = csv_path
        self.bbbs_column = bbbs_column
        self._df: pd.DataFrame | None = None
        self._load_csv()

    def _load_csv(self) -> None:
        """\u8bfb\u53d6 CSV \u6587\u4ef6\u5230\u5185\u5b58\uff0c\u4ee5 bbbs \u5217\u4e3a\u7d22\u5f15\u3002"""
        try:
            df = pd.read_csv(self.csv_path, dtype={self.bbbs_column: str})
            df = df.set_index(self.bbbs_column)
            self._df = df
            logger.info(f"CSV \u5143\u6570\u636e\u5df2\u52a0\u8f7d: {self.csv_path} ({len(df)} \u6761\u8bb0\u5f55)")
        except Exception as e:
            logger.warning(f"CSV \u5143\u6570\u636e\u52a0\u8f7d\u5931\u8d25 ({self.csv_path}): {e}")
            self._df = None

    def _column_to_metadata_key(self, column: str) -> str:
        """\u5c06 CSV \u5217\u540d\u8f6c\u4e3a\u5c0f\u5199 snake_case \u7684 metadata key\u3002"""
        return column.strip().replace(" ", "_").lower()

    def _enrich_impl(self, chunk, document):
        """\u4ece document.metadata["bbbs"] \u67e5\u627e CSV \u8bb0\u5f55\uff0c\u6ce8\u5165 chunk.metadata\u3002"""
        if self._df is None:
            return chunk

        bbbs = document.metadata.get("bbbs")
        if bbbs is None:
            return chunk

        try:
            row = self._df.loc[str(bbbs)]
            for column in self._df.columns:
                key = self._column_to_metadata_key(column)
                value = row[column]
                if pd.notna(value):
                    chunk.metadata[key] = value
        except KeyError:
            pass

        return chunk