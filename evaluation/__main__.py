"""rag-eval CLI 入口。

允许通过 python -m evaluation <command> 直接调用。
"""

from evaluation.cli import cli

if __name__ == "__main__":
    cli()
