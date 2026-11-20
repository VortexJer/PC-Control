/* PC-Control: thread-specific pre-activation guard. No global input hook.
 * Windows loads this DLL only into explicitly selected target GUI threads.
 * It never injects keys/mouse events, activates a window, or changes z-order.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

static HINSTANCE instance;
static volatile LONG blocked_activation, blocked_focus;

static LRESULT CALLBACK probe_proc(int code, WPARAM removed, LPARAM data) {
    if (code == HC_ACTION && removed == PM_REMOVE && data) {
        MSG *message = (MSG *)data;
        /* Inspect only our tagged WM_NULL heartbeat, never input contents. */
        if (message->message == WM_NULL && message->wParam == 0x50434342 && message->hwnd)
            SetPropW(message->hwnd, L"PC-Control.FocusGuardReady", (HANDLE)(ULONG_PTR)message->lParam);
    }
    return CallNextHookEx(NULL, code, removed, data);
}

__declspec(dllexport) LONG pc_focus_blocked(int focus) {
    return focus ? blocked_focus : blocked_activation;
}

/* Separate pure decision so the same binary's policy can be tested without
 * synthesizing real user input. A real click or shell keyboard switch wins. */
__declspec(dllexport) int pc_focus_allow(int foreground_tree, int mouse_activation,
                                        int shell_switch, int no_foreground) {
    return foreground_tree || mouse_activation || shell_switch || no_foreground;
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
