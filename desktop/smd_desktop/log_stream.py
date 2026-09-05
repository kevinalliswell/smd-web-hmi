"""将已有 structlog PrintLogger 输出写入 Windows Service 轮转日志。"""

import logging


class LogStream:
    encoding = "utf-8"

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.pending = ""

    def write(self, text: str):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line:
                self.logger.info(line)
        return len(text)

    def flush(self):
        if self.pending:
            line, self.pending = self.pending, ""
            self.logger.info(line)

    def isatty(self):
        return False
