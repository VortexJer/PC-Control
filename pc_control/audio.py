"""The system volume, through Windows' own Core Audio API (the same the taskbar's volume uses): no flyout, no window, no focus.

Interfaces declared here with comtypes (already a dependency through uiautomation) as in pycaw: IMMDeviceEnumerator -> default render
endpoint -> IAudioEndpointVolume. Levels are the 0..100 the taskbar shows (SetMasterVolumeLevelScalar is the audio-tapered scalar
the Windows volume slider uses)."""
from ctypes import HRESULT, POINTER, c_float, c_int, c_uint, c_void_p
from ctypes.wintypes import BOOL, DWORD, LPCWSTR
import comtypes
from comtypes import COMMETHOD, GUID, IUnknown

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class IAudioEndpointVolume(IUnknown):
    _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    _methods_ = [
        COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
        COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
        COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(c_uint), "pnChannelCount")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out"], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], c_uint, "nChannel"), (["in"], c_float, "fLevelDB"),
                  (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], c_uint, "nChannel"), (["in"], c_float, "fLevel"),
                  (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "pbMute")),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "dwClsCtx"),
                  (["in"], c_void_p, "pActivationParams"), (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_int, "dataFlow"), (["in"], DWORD, "dwStateMask"),
                  (["out"], POINTER(c_void_p), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_int, "dataFlow"), (["in"], c_int, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
    ]


def _endpoint():
    comtypes.CoInitialize()
    en = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    dev = en.GetDefaultAudioEndpoint(0, 1)                         # eRender, eMultimedia: what the taskbar volume controls
    unk = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return unk.QueryInterface(IAudioEndpointVolume)


def get():
    """(level 0..100, muted) of the default output, as the taskbar shows it."""
    v = _endpoint()
    return round(v.GetMasterVolumeLevelScalar() * 100), bool(v.GetMute())


def set_level(level):
    v = _endpoint()
    v.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(level) / 100.0)), None)
    return get()


def set_mute(mute):
    v = _endpoint()
    v.SetMute(bool(mute), None)
    return get()
