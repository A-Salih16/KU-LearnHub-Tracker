"""Encrypt the Moodle token for the current Windows user using built-in DPAPI."""
import base64
import ctypes
from ctypes import wintypes

class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]

def _convert(raw, encrypt):
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)

def protect(text):
    return base64.b64encode(_convert(text.encode(), True)).decode()

def unprotect(text):
    return _convert(base64.b64decode(text), False).decode()
