"""A strict stand-in for the small part of Streamlit that app.py uses.

It is NOT Streamlit. It lets the test-suite execute app.py top-to-bottom in an environment where
Streamlit cannot be installed, and it fails loudly if app.py calls any API not listed here.
"""
import sys
import types
from contextlib import contextmanager


class RerunRequested(Exception):
    pass


class AppStopped(Exception):
    pass


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):          # columns / expanders accept the same calls as st.*
        return getattr(_STUB, name)


class SessionState(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


class Stub(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = SessionState()
        self.clicks = set()               # button labels "pressed" in the next run
        self.calls = []                   # (api, first-arg) log
        self.sidebar = _Ctx()

    def _log(self, name, *a):
        self.calls.append((name, a[0] if a else None))

    # ---- layout / text
    def set_page_config(self, **k): self._log("set_page_config")
    def title(self, t): self._log("title", t)
    def caption(self, t): self._log("caption", t)
    def header(self, t): self._log("header", t)
    def subheader(self, t): self._log("subheader", t)
    def write(self, *a): self._log("write", *a)
    def code(self, t, **k): self._log("code", t)
    def markdown(self, t, **k): self._log("markdown", t)
    def divider(self): pass
    def success(self, t): self._log("success", t)
    def info(self, t): self._log("info", t)
    def warning(self, t): self._log("warning", t)
    def error(self, t): self._log("error", t)
    def progress(self, v): assert 0 <= v <= 1, v
    def metric(self, label, value, delta=None, **k): self._log("metric", label)
    def dataframe(self, df, **k): self._log("dataframe", len(df))
    def bar_chart(self, df, **k): self._log("bar_chart", len(df))
    def map(self, df, **k):
        assert {"lat", "lon"} <= set(df.columns); self._log("map", len(df))
    def empty(self): return _Empty(self)
    def columns(self, spec): return [_Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]
    def tabs(self, names): return [_Ctx() for _ in names]
    def expander(self, label, expanded=False): return _Ctx()
    def stop(self):
        raise AppStopped()

    def container(self): return _Ctx()

    def rerun(self):
        self.clicks = set()           # like Streamlit: a button is True only in the run it triggered
        raise RerunRequested()

    # ---- inputs
    def _store(self, key, default):
        if key is None:
            return default
        return self.session_state.setdefault(key, default)

    def text_area(self, label, key=None, **k): return self._store(key, "")
    def text_input(self, label, value="", key=None, **k): return self._store(key, value)
    def checkbox(self, label, value=False, key=None, **k): return self._store(key, value)
    def radio(self, label, options, key=None, **k): return self._store(key, options[0])
    def selectbox(self, label, options, index=0, key=None, format_func=None, **k):
        options = list(options)
        val = self._store(key, options[index] if options else None)
        assert val in options, f"{label}: {val!r} not in {options}"
        return val
    def slider(self, label, lo, hi, value=None, key=None, **k): return self._store(key, value if value is not None else lo)
    def number_input(self, label, lo, hi, value, **k): return value
    def date_input(self, label, value=None, **k): return value

    def button(self, label, key=None, on_click=None, args=(), disabled=False, **k):
        pressed = label in self.clicks and not disabled
        if pressed and on_click:
            on_click(*args)
        return pressed and not on_click

    def download_button(self, label, data, file_name=None, **k):
        assert isinstance(data, (str, bytes)) and len(data) > 20
        self._log("download_button", label)
        return False


class _Empty:
    def __init__(self, st): self.st = st
    def markdown(self, t, **k): self.st._log("empty.markdown", t)
    def info(self, t): self.st._log("empty.info", t)
    def empty(self): pass


_STUB = Stub()


def install() -> Stub:
    """Register the stub as `streamlit` and return it (fresh state)."""
    global _STUB
    _STUB = Stub()
    sys.modules["streamlit"] = _STUB
    return _STUB
