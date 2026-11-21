/* 32-bit helper for the focus guard: Windows only lets a process install a hook DLL of ITS OWN architecture, so the 64-bit server
   cannot guard a 32-bit app (old apps, Notepad++ x86, 32-bit JREs running Oracle Forms...). This tiny 32-bit process loads
   focus_guard_x86.dll and installs/removes the same thread hooks on request. Protocol on stdin/stdout, one line each:
     i <tid>   install the activation guard on that thread  -> "<hook> <error>"
     p <tid>   install the short-lived acknowledgement probe -> "<hook> <error>"
     r <hook>  remove a hook                                -> "<0|1>"
   When the server goes away (stdin closes) the process exits and Windows removes every hook it installed. */
#include <windows.h>
#include <stdio.h>

typedef HHOOK (*install_fn)(DWORD);
typedef BOOL (*remove_fn)(HHOOK);

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    HMODULE dll = LoadLibraryA(argv[1]);
    if (!dll) { printf("load-failed %lu\n", GetLastError()); fflush(stdout); return 3; }
    install_fn install = (install_fn)GetProcAddress(dll, "pc_focus_install");
    install_fn probe = (install_fn)GetProcAddress(dll, "pc_focus_probe_install");
    remove_fn remove_ = (remove_fn)GetProcAddress(dll, "pc_focus_remove");
    if (!install || !probe || !remove_) { printf("bad-dll 0\n"); fflush(stdout); return 4; }
    printf("ready 0\n"); fflush(stdout);
    char line[128];
    while (fgets(line, sizeof line, stdin)) {
        unsigned long v = 0;
        char op = 0;
        if (sscanf(line, "%c %lu", &op, &v) != 2) { printf("0 87\n"); fflush(stdout); continue; }
        if (op == 'i' || op == 'p') {
            SetLastError(0);
            HHOOK h = (op == 'i' ? install : probe)((DWORD)v);
            printf("%lu %lu\n", (unsigned long)(ULONG_PTR)h, h ? 0UL : GetLastError());
        } else if (op == 'r') {
            printf("%d\n", remove_((HHOOK)(ULONG_PTR)v) ? 1 : 0);
        } else {
            printf("0 87\n");
        }
        fflush(stdout);
    }
    return 0;
}
