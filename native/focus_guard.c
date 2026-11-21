/* PC-Control: thread-specific pre-activation guard. No global input hook.
 * Windows loads this DLL only into explicitly selected target GUI threads.
 * It never injects keys/mouse events or changes z-order; the only window it ever brings to the foreground is the user's own
 * (hand_back and the give-back message), and only after a target app took it without the user asking.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

static HINSTANCE instance;
static volatile LONG blocked_activation, blocked_focus;

static HWND restore_target(void);

static LRESULT CALLBACK probe_proc(int code, WPARAM removed, LPARAM data) {
    if (code == HC_ACTION && removed == PM_REMOVE && data) {
        MSG *message = (MSG *)data;
        /* Inspect only our tagged WM_NULL messages, never input contents. */
        if (message->message == WM_NULL && message->wParam == 0x50434342 && message->hwnd)
            SetPropW(message->hwnd, L"PC-Control.FocusGuardReady", (HANDLE)(ULONG_PTR)message->lParam);
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
