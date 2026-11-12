import logging
import re


class PrivatePathFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(
            r'(/(?:gallery|admin/reset)/)[^\s?"<>]+', r"\1[redacted]", message
        )
        record.args = ()
        return True
