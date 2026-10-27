"""``logging`` integration: records >= event_level become events, lower ones become breadcrumbs."""
import logging as _logging

import truti


class TrutiHandler(_logging.Handler):
    def __init__(self, level=_logging.NOTSET, event_level=_logging.ERROR, breadcrumb_level=_logging.INFO):
        super().__init__(level)
        self.event_level = event_level
        self.breadcrumb_level = breadcrumb_level

    def emit(self, record):
        try:
            if record.name == "truti" or record.name.startswith("truti."):
                return  # never feed the SDK's own logs back into itself
            msg = record.getMessage()
            if record.levelno >= self.event_level:
                level = "fatal" if record.levelno >= _logging.CRITICAL else "error"
                if record.exc_info and record.exc_info[0] is not None:
                    truti.capture_exception(record.exc_info, level=level)
                else:
                    truti.capture_message(msg, level=level)
            elif record.levelno >= self.breadcrumb_level:
                truti.add_breadcrumb(msg, category=record.name, level=record.levelname.lower())
        except Exception:
            pass


def install(logger=None, **kw):
    handler = TrutiHandler(**kw)
    (logger or _logging.getLogger()).addHandler(handler)
    return handler
