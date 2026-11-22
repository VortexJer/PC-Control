/* PC-Control: thread-specific pre-activation guard. No global input hook.
 * Windows loads this DLL only into explicitly selected target GUI threads.
 * It never injects keys/mouse events into the system or changes z-order (the redirects below only change what THIS app is told
 * while PC-Control delivers its own click or key); the only window it ever brings to the foreground is the user's own
 * (hand_back and the give-back message), and only after a target app took it without the user asking.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

/* kernel32 export (Windows 7+); declared here so no psapi import library is needed */
WINBASEAPI BOOL WINAPI K32EnumProcessModules(HANDLE process, HMODULE *modules, DWORD cb, LPDWORD needed);

static HINSTANCE instance;
static volatile LONG blocked_activation, blocked_focus;

static HWND restore_target(void);

/* ---------- Claude's pointer and keys, as the app sees them ----------
 * Some apps (Blender, game engines) do not take a click's position from the message: they ask Windows where the mouse is
 * (GetCursorPos / GetMessagePos), so a click PC-Control sends by message landed where the USER's mouse was. And some read the
 * keyboard only through Raw Input (WM_INPUT + GetRawInputData), which Windows delivers only to the foreground window, so no key
 * message ever reaches them. While PC-Control delivers its own click or key to this process, and only then:
 *   - GetCursorPos / GetPhysicalCursorPos / GetMessagePos / GetCursorInfo answer with Claude's point;
 *   - a WM_INPUT that PC-Control posted carries a handle of its own (RAW_MAGIC + slot) and GetRawInputData answers it with Claude's
 *     key record from that slot; every real handle goes to Windows unchanged;
 *   - SetCursorPos / ClipCursor from this app cannot move or confine the user's mouse while the user works in another app;
 *   - ToUnicodeEx gives the character Claude meant for that key record (accents and any Unicode, whatever the layout);
 *   - GetAsyncKeyState / GetKeyState / GetKeyboardState report Claude's modifier and button state (the user's held Shift does not
 *     leak into Claude's typing, and Claude's never reaches the user's apps: only this process is told).
 * PC-Control publishes all of it in "Local\PC-Control.VPointer.<pid>" with expiry ticks; outside them every call goes to Windows
 * unchanged. The real cursor never moves and nothing is injected into the system: only this app's questions are answered.
 * The functions are redirected in this process's import tables only (no code patching), and the DLL pins itself so a redirected
 * entry can never point into an unloaded module. */
#define RAW_MAGIC ((ULONG_PTR)0x7EC0DE00u)
#define RAW_SLOTS 64
typedef struct {
    volatile LONG x, y; volatile DWORD until; volatile LONG used;      /* pointer (offsets 0..15) */
    volatile DWORD keys_until; volatile LONG raw_done;                   /* keys (16..23) */
    BYTE keys[256];                                                      /* Claude's key/button state, 0x80 = down (24..279) */
    RAWKEYBOARD ring[RAW_SLOTS];                                         /* key records for posted WM_INPUT (280..1303) */
    volatile LONG cur_vk; volatile LONG cur_char;                        /* the record being handled now (1304..1311) */
} VPOINT;
static VPOINT *vp;
static volatile LONG vp_patched;
static DWORD vp_last_try;
static BOOL (WINAPI *real_GetCursorPos)(LPPOINT);
static BOOL (WINAPI *real_GetPhysicalCursorPos)(LPPOINT);
static DWORD (WINAPI *real_GetMessagePos)(void);
static BOOL (WINAPI *real_GetCursorInfo)(PCURSORINFO);
static UINT (WINAPI *real_GetRawInputData)(HRAWINPUT, UINT, LPVOID, PUINT, UINT);
static SHORT (WINAPI *real_GetAsyncKeyState)(int);
static SHORT (WINAPI *real_GetKeyState)(int);
static BOOL (WINAPI *real_GetKeyboardState)(PBYTE);
static int (WINAPI *real_ToUnicodeEx)(UINT, UINT, const BYTE *, LPWSTR, int, UINT, HKL);

static VPOINT *vpoint(void) {
    if (vp) return vp;
    DWORD now = GetTickCount();
    if (vp_last_try && now - vp_last_try < 500) return NULL;      /* cheap: GetCursorPos may be called every frame */
    vp_last_try = now ? now : 1;
    WCHAR name[64];
    wsprintfW(name, L"Local\\PC-Control.VPointer.%lu", GetCurrentProcessId());
    HANDLE map = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name);
    if (!map) return NULL;
    VPOINT *view = (VPOINT *)MapViewOfFile(map, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(VPOINT));
    CloseHandle(map);                                               /* the view keeps the section alive */
    if (view && InterlockedCompareExchangePointer((PVOID volatile *)&vp, view, NULL) != NULL) UnmapViewOfFile(view);
    return vp;
}

static int live(DWORD until) { return until && (LONG)(until - GetTickCount()) > 0; }

static int vactive(POINT *p) {
    VPOINT *v = vpoint();
    if (!v || !live(v->until)) return 0;
    p->x = v->x; p->y = v->y;
    InterlockedIncrement(&v->used);
    return 1;
}

/* keys whose state is Claude's alone while Claude delivers input: modifiers and mouse buttons */
static int owned_vk(int vk) {
    switch (vk) {
    case VK_SHIFT: case VK_LSHIFT: case VK_RSHIFT: case VK_CONTROL: case VK_LCONTROL: case VK_RCONTROL:
    case VK_MENU: case VK_LMENU: case VK_RMENU: case VK_LWIN: case VK_RWIN:
    case VK_LBUTTON: case VK_RBUTTON: case VK_MBUTTON: return 1;
    default: return 0;
    }
}

static VPOINT *keys_live(void) {
    VPOINT *v = vp ? vp : vpoint();
    return (v && live(v->keys_until)) ? v : NULL;
}

static BOOL WINAPI hook_GetCursorPos(LPPOINT p) {
    POINT q;
    if (p && vactive(&q)) { *p = q; return TRUE; }
    return real_GetCursorPos(p);
}

static BOOL WINAPI hook_GetPhysicalCursorPos(LPPOINT p) {
    POINT q;
    if (p && vactive(&q)) { *p = q; return TRUE; }
    return real_GetPhysicalCursorPos(p);
}

static DWORD WINAPI hook_GetMessagePos(void) {
    POINT q;
    if (vactive(&q)) return (DWORD)MAKELONG((SHORT)q.x, (SHORT)q.y);
    return real_GetMessagePos();
}

static BOOL WINAPI hook_GetCursorInfo(PCURSORINFO info) {
    BOOL ok = real_GetCursorInfo(info);
    POINT q;
    if (ok && info && vactive(&q)) info->ptScreenPos = q;
    return ok;
}

static UINT WINAPI hook_GetRawInputData(HRAWINPUT h, UINT cmd, LPVOID data, PUINT size, UINT header_size) {
    ULONG_PTR v = (ULONG_PTR)h;
    if ((v & ~(ULONG_PTR)(RAW_SLOTS - 1)) == RAW_MAGIC) {
        VPOINT *p = vp ? vp : vpoint();
        if (!p || !size || header_size != sizeof(RAWINPUTHEADER) || (cmd != RID_INPUT && cmd != RID_HEADER)) {
            SetLastError(ERROR_INVALID_HANDLE); return (UINT)-1;
        }
        UINT need = sizeof(RAWINPUTHEADER) + (cmd == RID_INPUT ? sizeof(RAWKEYBOARD) : 0);
        if (!data) { *size = need; return 0; }
        if (*size < need) { *size = need; SetLastError(ERROR_INSUFFICIENT_BUFFER); return (UINT)-1; }
        RAWINPUT *r = (RAWINPUT *)data;
        r->header.dwType = RIM_TYPEKEYBOARD;
        r->header.dwSize = sizeof(RAWINPUTHEADER) + sizeof(RAWKEYBOARD);
        r->header.hDevice = NULL;
        r->header.wParam = RIM_INPUT;
        if (cmd == RID_INPUT) {
            r->data.keyboard = p->ring[v & (RAW_SLOTS - 1)];
            /* the character Claude means by this key (ExtraInformation), answered by ToUnicodeEx below: accents, AltGr signs
             * and any Unicode come out exactly, whatever the keyboard layout and its dead-key state */
            p->cur_vk = r->data.keyboard.VKey;
            p->cur_char = (LONG)r->data.keyboard.ExtraInformation;
            r->data.keyboard.ExtraInformation = 0;
            InterlockedIncrement(&p->raw_done);
        }
        return need;
    }
    return real_GetRawInputData(h, cmd, data, size, header_size);
}

static SHORT WINAPI hook_GetAsyncKeyState(int vk) {
    SHORT real = real_GetAsyncKeyState(vk);
    VPOINT *p = keys_live();
    if (!p || vk < 0 || vk > 255) return real;
    if (owned_vk(vk)) return (p->keys[vk] & 0x80) ? (SHORT)0x8000 : 0;
    return (p->keys[vk] & 0x80) ? (SHORT)(real | 0x8000) : real;
}

static SHORT WINAPI hook_GetKeyState(int vk) {
    SHORT real = real_GetKeyState(vk);
    VPOINT *p = keys_live();
    if (!p || vk < 0 || vk > 255) return real;
    if (owned_vk(vk)) return (SHORT)(((p->keys[vk] & 0x80) ? 0x8000 : 0) | (real & 1));
    return (p->keys[vk] & 0x80) ? (SHORT)(real | 0x8000) : real;
}

static BOOL WINAPI hook_GetKeyboardState(PBYTE state) {
    BOOL ok = real_GetKeyboardState(state);
    VPOINT *p = keys_live();
    if (ok && state && p) {
        for (int vk = 0; vk < 256; ++vk) {
            if (owned_vk(vk)) state[vk] = (BYTE)((state[vk] & 1) | (p->keys[vk] & 0x80));
            else state[vk] |= (BYTE)(p->keys[vk] & 0x80);
        }
    }
    return ok;
}

static int WINAPI hook_ToUnicodeEx(UINT vk, UINT scan, const BYTE *state, LPWSTR buf, int cch, UINT flags, HKL hkl) {
    VPOINT *p = keys_live();
    if (p && p->cur_char && (UINT)p->cur_vk == vk && buf && cch > 0) {
        WCHAR a = (WCHAR)(p->cur_char & 0xFFFF), b = (WCHAR)(((ULONG)p->cur_char >> 16) & 0xFFFF);
        buf[0] = a;
        if (b && cch > 1) { buf[1] = b; return 2; }
        return 1;
    }
    return real_ToUnicodeEx(vk, scan, state, buf, cch, flags, hkl);
}

/* An app that is NOT where the user works (another process has the foreground) may not move or confine the user's mouse: a tool
 * started by Claude in a 3D app, a game's mouse-look, a "wrap the cursor" drag. The app is told it worked and sees the point it
 * asked for (Claude's pointer takes it), the real cursor stays where the user has it. In the app the user is using, nothing changes. */
static BOOL (WINAPI *real_SetCursorPos)(int, int);
static BOOL (WINAPI *real_SetPhysicalCursorPos)(int, int);
static BOOL (WINAPI *real_ClipCursor)(const RECT *);

static int user_elsewhere(void) {
    HWND fg = GetForegroundWindow();
    DWORD pid = 0;
    if (fg) GetWindowThreadProcessId(fg, &pid);
    return pid != GetCurrentProcessId();
}

static BOOL contain_warp(int x, int y) {
    VPOINT *v = vp ? vp : vpoint();
    if (v) { v->x = x; v->y = y; v->until = GetTickCount() + 1500; if (!v->until) v->until = 1; }
    return TRUE;
}

static BOOL WINAPI hook_SetCursorPos(int x, int y) {
    return user_elsewhere() ? contain_warp(x, y) : real_SetCursorPos(x, y);
}

static BOOL WINAPI hook_SetPhysicalCursorPos(int x, int y) {
    return user_elsewhere() ? contain_warp(x, y) : real_SetPhysicalCursorPos(x, y);
}

static BOOL WINAPI hook_ClipCursor(const RECT *r) {
    if (r && user_elsewhere()) return TRUE;                         /* releasing (NULL) is always let through */
    return real_ClipCursor(r);
}

#define N_HOOKS 12
static void patch_module(HMODULE module) {
    BYTE *base = (BYTE *)module;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress || !dir.Size) return;
    void *real[N_HOOKS] = {(void *)real_GetCursorPos, (void *)real_GetPhysicalCursorPos, (void *)real_GetMessagePos,
                           (void *)real_GetCursorInfo, (void *)real_GetRawInputData, (void *)real_GetAsyncKeyState,
                           (void *)real_GetKeyState, (void *)real_GetKeyboardState, (void *)real_ToUnicodeEx,
                           (void *)real_SetCursorPos, (void *)real_SetPhysicalCursorPos, (void *)real_ClipCursor};
    void *mine[N_HOOKS] = {(void *)hook_GetCursorPos, (void *)hook_GetPhysicalCursorPos, (void *)hook_GetMessagePos,
                           (void *)hook_GetCursorInfo, (void *)hook_GetRawInputData, (void *)hook_GetAsyncKeyState,
                           (void *)hook_GetKeyState, (void *)hook_GetKeyboardState, (void *)hook_ToUnicodeEx,
                           (void *)hook_SetCursorPos, (void *)hook_SetPhysicalCursorPos, (void *)hook_ClipCursor};
    for (IMAGE_IMPORT_DESCRIPTOR *d = (IMAGE_IMPORT_DESCRIPTOR *)(base + dir.VirtualAddress); d->Name; ++d) {
        if (!d->FirstThunk) continue;
        for (IMAGE_THUNK_DATA *t = (IMAGE_THUNK_DATA *)(base + d->FirstThunk); t->u1.Function; ++t) {
            for (int i = 0; i < N_HOOKS; ++i) {
                /* only an entry that already holds the exact address Windows resolved for that function is redirected */
                if (real[i] && (void *)t->u1.Function == real[i]) {
                    DWORD old;
                    if (VirtualProtect(&t->u1.Function, sizeof(t->u1.Function), PAGE_READWRITE, &old)) {
                        t->u1.Function = (ULONG_PTR)mine[i];
                        VirtualProtect(&t->u1.Function, sizeof(t->u1.Function), old, &old);
                    }
                }
            }
        }
    }
}

/* Runs on the app's UI thread (from the message hook), never under the loader lock. Idempotent: modules loaded since the last
 * call are redirected too; an entry already redirected no longer holds the real address and is left alone. */
static void patch_process(void) {
    if (!vp_patched) {
        HMODULE pinned, user32 = GetModuleHandleW(L"user32.dll");
        if (!user32) return;
        real_GetCursorPos = (BOOL (WINAPI *)(LPPOINT))GetProcAddress(user32, "GetCursorPos");
        real_GetPhysicalCursorPos = (BOOL (WINAPI *)(LPPOINT))GetProcAddress(user32, "GetPhysicalCursorPos");
        real_GetMessagePos = (DWORD (WINAPI *)(void))GetProcAddress(user32, "GetMessagePos");
        real_GetCursorInfo = (BOOL (WINAPI *)(PCURSORINFO))GetProcAddress(user32, "GetCursorInfo");
        real_GetRawInputData = (UINT (WINAPI *)(HRAWINPUT, UINT, LPVOID, PUINT, UINT))GetProcAddress(user32, "GetRawInputData");
        real_GetAsyncKeyState = (SHORT (WINAPI *)(int))GetProcAddress(user32, "GetAsyncKeyState");
        real_GetKeyState = (SHORT (WINAPI *)(int))GetProcAddress(user32, "GetKeyState");
        real_GetKeyboardState = (BOOL (WINAPI *)(PBYTE))GetProcAddress(user32, "GetKeyboardState");
        real_ToUnicodeEx = (int (WINAPI *)(UINT, UINT, const BYTE *, LPWSTR, int, UINT, HKL))GetProcAddress(user32, "ToUnicodeEx");
        real_SetCursorPos = (BOOL (WINAPI *)(int, int))GetProcAddress(user32, "SetCursorPos");
        real_SetPhysicalCursorPos = (BOOL (WINAPI *)(int, int))GetProcAddress(user32, "SetPhysicalCursorPos");
        real_ClipCursor = (BOOL (WINAPI *)(const RECT *))GetProcAddress(user32, "ClipCursor");
        if (!real_GetCursorPos || !real_GetMessagePos || !real_GetRawInputData || !real_GetAsyncKeyState ||
            !real_GetKeyState || !real_GetKeyboardState || !real_ToUnicodeEx || !real_SetCursorPos || !real_ClipCursor)
            return;
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN, (LPCWSTR)&hook_GetCursorPos, &pinned))
            return;                                                      /* never redirect without the pin */
        vp_patched = 1;
    }
    HMODULE mods[1024];
    DWORD need = 0;
    if (!K32EnumProcessModules(GetCurrentProcess(), mods, sizeof(mods), &need)) return;
    DWORD n = need / sizeof(HMODULE);
    if (n > 1024) n = 1024;
    for (DWORD i = 0; i < n; ++i)
        if (mods[i] != instance) patch_module(mods[i]);
}

__declspec(dllexport) LONG pc_vpointer_patched(void) { return vp_patched; }

static LRESULT CALLBACK probe_proc(int code, WPARAM removed, LPARAM data) {
    if (code == HC_ACTION && removed == PM_REMOVE && data) {
        MSG *message = (MSG *)data;
        /* Inspect only our tagged WM_NULL messages, never input contents. */
        if (message->message == WM_NULL && message->wParam == 0x50434342 && message->hwnd)
            SetPropW(message->hwnd, L"PC-Control.FocusGuardReady", (HANDLE)(ULONG_PTR)message->lParam);
        /* 'PCCV': PC-Control is about to deliver its pointer to this process (see vpoint) */
        if (message->message == WM_NULL && message->wParam == 0x50434356 && message->hwnd) {
            patch_process();
            /* tell PC-Control whether this app reads the keyboard through Raw Input (then key messages do not reach it) */
            RAWINPUTDEVICE devs[32];
            UINT n = 32, raw_kb = 0;
            UINT got = GetRegisteredRawInputDevices(devs, &n, sizeof(RAWINPUTDEVICE));
            for (UINT i = 0; got != (UINT)-1 && i < got; ++i)
                if (devs[i].usUsagePage == 0x01 && devs[i].usUsage == 0x06) raw_kb = (devs[i].dwFlags & RIDEV_NOLEGACY) ? 2 : 1;
            SetPropW(message->hwnd, L"PC-Control.RawKeyboard", (HANDLE)(ULONG_PTR)(raw_kb + 1));
            SetPropW(message->hwnd, L"PC-Control.VPointerReady", (HANDLE)(ULONG_PTR)message->lParam);
        }
        /* Give-back: this app's NEW window took the foreground although the user did not ask for it (a Store app's frame is
         * activated by a thread the pre-activation hook could not reach in time). This process owns the foreground, so Windows lets
         * it hand it back - only to the user's own window, only while this window is still the foreground. */
        if (message->message == WM_NULL && message->wParam == 0x50434347 && message->hwnd) {
            HWND fg = GetForegroundWindow(), target = restore_target();
            if (fg && target && IsWindow(target) && IsWindowVisible(target) &&
                GetAncestor(fg, GA_ROOT) == GetAncestor(message->hwnd, GA_ROOT) &&
                GetAncestor(target, GA_ROOT) != GetAncestor(message->hwnd, GA_ROOT))
                SetForegroundWindow(target);
        }
    }
    return CallNextHookEx(NULL, code, removed, data);
}

__declspec(dllexport) LONG pc_focus_blocked(int focus) {
    return focus ? blocked_focus : blocked_activation;
}

/* Separate pure decision so the same binary's policy can be tested without
 * synthesizing real user input. A real click or shell keyboard switch wins.
 * An EMPTY foreground is not a reason to allow: an app that calls
 * SetForegroundWindow while starting (Java does) first empties the foreground
 * and then activates itself; allowing that second step handed it the user's
 * keyboard. no_foreground is kept in the signature for the policy tests. */
__declspec(dllexport) int pc_focus_allow(int foreground_tree, int mouse_activation,
                                        int shell_switch, int no_foreground) {
    (void)no_foreground;
    return foreground_tree || mouse_activation || shell_switch;
}

__declspec(dllexport) int pc_focus_owned(HWND target, HWND foreground) {
    if (!target || !foreground) return 0;
    HWND roots[2] = {GetAncestor(target, GA_ROOT), GetAncestor(foreground, GA_ROOT)};
    for (int side = 0; side < 2; ++side) {
        for (int depth = 0; depth < 32; ++depth) {
            HWND owner = GetWindow(roots[side], GW_OWNER);
            /* Frameworks can give unrelated document windows one hidden owner.
             * That must not make sibling windows share foreground permission. */
            if (!owner || !IsWindowVisible(owner)) break;
            HWND root = GetAncestor(owner, GA_ROOT);
            if (!root || root == roots[side]) break;
            roots[side] = root;
        }
    }
    return roots[0] && roots[0] == roots[1];
}

static int user_switching(void) {
    return (GetAsyncKeyState(VK_MENU) & 0x8000) ||
           (GetAsyncKeyState(VK_LWIN) & 0x8000) ||
           (GetAsyncKeyState(VK_RWIN) & 0x8000);
}

/* The window the user had when PC-Control started acting, published by PC-Control in a named shared section.
 * Only read here. */
static HWND restore_target(void) {
    HANDLE map = OpenFileMappingW(FILE_MAP_READ, FALSE, L"Local\\PC-Control.FocusRestore");
    if (!map) return NULL;
    HWND target = NULL;
    HWND *view = (HWND *)MapViewOfFile(map, FILE_MAP_READ, 0, 0, sizeof(HWND));
    if (view) { target = *view; UnmapViewOfFile(view); }
    CloseHandle(map);
    return target;
}

/* The ONE place that may set the foreground: an app that called SetForegroundWindow while starting has
 * already EMPTIED the foreground when its activation is refused here. While it is empty, and only then,
 * the user's own window gets it back. This runs inside the app, which Windows lets set the foreground;
 * PC-Control's process often may not. Never another window, never while someone owns the foreground. */
static void hand_back(HWND refused) {
    if (GetForegroundWindow()) return;
    HWND target = restore_target();
    if (!target || !IsWindow(target) || !IsWindowVisible(target)) return;
    if (GetAncestor(target, GA_ROOT) == GetAncestor(refused, GA_ROOT)) return;
    SetForegroundWindow(target);
}

static LRESULT CALLBACK guard_proc(int code, WPARAM w, LPARAM l) {
    if (code == HCBT_ACTIVATE || code == HCBT_SETFOCUS) {
        if (!w) return CallNextHookEx(NULL, code, w, l); /* releasing focus is not acquiring it */
        HWND fg = GetForegroundWindow();
        int mouse = code == HCBT_ACTIVATE && l && ((CBTACTIVATESTRUCT *)l)->fMouse;
        if (code == HCBT_ACTIVATE && (mouse || user_switching()))
            SetPropW((HWND)w, L"PC-Control.UserActivation", (HANDLE)(ULONG_PTR)GetTickCount());
        int owned = pc_focus_owned((HWND)w, fg);
        if (!pc_focus_allow(owned, mouse,
                            user_switching(), fg == NULL)) {
            InterlockedIncrement(code == HCBT_ACTIVATE ? &blocked_activation : &blocked_focus);
            /* Tell PC-Control (it reads window properties) that this app tried. */
            SetPropW(GetAncestor((HWND)w, GA_ROOT), L"PC-Control.Denied", (HANDLE)(ULONG_PTR)GetTickCount());
            if (code == HCBT_ACTIVATE) hand_back((HWND)w);
            return 1; /* Deny before the foreground or keyboard focus changes. */
        }
    }
    return CallNextHookEx(NULL, code, w, l);
}

__declspec(dllexport) HHOOK pc_focus_install(DWORD thread_id) {
    if (!thread_id) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (!instance && !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                         GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                         (LPCWSTR)&guard_proc, &instance))
        return NULL;
    return SetWindowsHookExW(WH_CBT, guard_proc, instance, thread_id);
}

__declspec(dllexport) BOOL pc_focus_remove(HHOOK hook) {
    return UnhookWindowsHookEx(hook);
}

__declspec(dllexport) HHOOK pc_focus_probe_install(DWORD thread_id) {
    if (!thread_id) { SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (!instance && !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                         GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                         (LPCWSTR)&probe_proc, &instance))
        return NULL;
    return SetWindowsHookExW(WH_GETMESSAGE, probe_proc, instance, thread_id);
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        instance = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}
