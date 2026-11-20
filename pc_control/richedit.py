"""RichEdit Text Object Model: edit an independent range, never the user's selection."""
import win32gui


def _document(hwnd):
    import pythoncom
    import win32com.client
    token = win32gui.SendMessage(hwnd, 0x003D, 0, -16)  # WM_GETOBJECT, OBJID_NATIVEOM
    if not token:
        return None
    return win32com.client.Dispatch(pythoncom.ObjectFromLresult(token, pythoncom.IID_IDispatch, 0))


def type_text(hwnd, text, replace=False):
    """None = no editable TOM interface, safe to try another backend.

    Once SetText is attempted, a failure is NOT silently retried with messages:
    an RPC error could mean the write arrived but its acknowledgement did not.
    """
    import pythoncom
    import pywintypes
    initialized = False
    doc = span = None
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except pywintypes.com_error as error:
            if error.hresult != -2147417850:  # RPC_E_CHANGED_MODE: use the existing apartment
                return None
        try:
            doc = _document(hwnd)
            if doc is None:
                return None
            # Let this editor clamp EOF in its own UTF-16/story model. Modern
            # RichEdit's WM_GETTEXTLENGTH can disagree with its TOM positions.
            end = 0x7FFFFFFF
            span = doc.Range(0 if replace else end, end)
        except Exception:
            return None
        value = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")
        try:
            span.Text = value
        except pywintypes.com_error as error:
            if error.hresult == -2147024891:  # E_ACCESSDENIED: write-protected range
                return "did not type: this RichEdit range is write-protected"
            return "WARNING: typing UNCONFIRMED after a RichEdit COM error; do not retry automatically"
        except Exception:
            return "WARNING: typing UNCONFIRMED after a RichEdit range error; do not retry automatically"
        try:
            if span.Text != value:
                return "WARNING: typing UNCONFIRMED: RichEdit range readback differs; do not retry automatically"
        except Exception:
            return "WARNING: typing UNCONFIRMED: RichEdit range could not be read back; do not retry automatically"
        return "ok (RichEdit TOM range; text read back; no selection command used)"
    finally:
        span = doc = None
        if initialized:
            pythoncom.CoUninitialize()
