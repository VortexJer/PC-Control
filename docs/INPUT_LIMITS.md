# Input limitations audit

Scope: investigate every item in README's Known limits, prioritizing modern text fields and drawing without using the user's keyboard or pointer. Tests used the real visible desktop. No hidden desktop tests were run for this change.

## Focus fix and RichEdit follow-up

The follow-up request prioritized focus prevention before continuing inputs. The original below-and-restore behavior only reacted after activation, so it could not exclude a brief focus change. It has been replaced for supported target threads by a native, thread-specific CBT veto, covering startup and visible mutation transactions. Z-order restoration now always uses `SWP_NOACTIVATE`. The startup watcher stops pushing/minimizing windows that the user deliberately activates.

The native guard is built from `native/focus_guard.c` and ships in the Python package. It has no system-wide hook, keyboard hook, pointer injection or elevation. It distinguishes top-level windows sharing a process, permits the current foreground window's visible ownership tree (including returning from its dialogs), and does not unify unrelated windows through a shared hidden framework owner. Mouse activation and shell switching while Alt/Windows is held are allowed. That preference for user switching means a concurrent programmatic activation during those modifier gestures is not strictly distinguishable. Windows rejects installation before a target GUI queue exists; retries cover initialization, but broker hand-offs/new GUI threads are not a universal race-free guarantee. Protected processes and incompatible architectures can refuse hooks; visible guarded mutation actions then refuse. The startup monitor is bounded, not a permanent policy for the lifetime of every third-party app.

A successful hook handle alone is insufficient. A temporary thread-specific message hook verifies in-process delivery using a tagged `WM_NULL` heartbeat and a matching acknowledgement. It reads no keyboard/mouse payload, forwards all messages, and is removed immediately. A missing acknowledgement refuses the action and removes partial hooks.

Verification on this x64 machine:

- A real visible, ordinary Win32 window deliberately tried activation/focus 24 times at startup and 24 more inside the MCP mutation wrapper. The native counters recorded vetoes; WinEvent observation recorded no foreground/focus event targeting the window or its editor.
- Real modern Notepad and Paint launches recorded zero foreground activations in the measured experiments. The Notepad write phase also recorded no foreground activation. These are measured application cases, not proof about every app/version or a sustained human input stress test.
- Notepad now accepts direct TOM range writes through its native object model. The exact preexisting Unicode text and line breaks were preserved before appending; selection `(2,6)` remained intact. The code sends no selection command for this route and lets the editor resolve EOF positions. Full replacements can naturally update positions of ranges that overlap the replaced content.
- Enter and Space in RichEdit documents also use ranges and preserved selection `(2,6)` in the real Notepad experiment. Movement/deletion keys refuse to act at the user's caret; explicit dialogs are handled separately. A GUI thread's focused control is accepted only inside the requested window/owned dialog, not another window sharing that thread. Chromium fallback also requires a single visible input host.
- Clicks/invocations/shortcuts in the user's currently busy foreground window yield. Unknown documents/tabs in RichEdit refuse typing after a title change; an unsaved `*` marker alone is tolerated. This reduces mistaken-target cases; a title is not a unique document identifier and is not a transaction lock against every possible tab-switch race.

Paint drawing is still unsupported by the background-message backend: zero canvas pixels changed in the original experiment; the backend now refuses that known case. No touch/mouse injection was added to force it.

The x64 DLL was executed and tested. The ARM64 DLL was cross-compiled, its PE architecture and source/binary hashes checked, but it was not executed on ARM hardware. Rebuild with `python tools/build_focus_guard.py --zig <path-to-zig.exe>`; add `--target aarch64-windows-gnu` for ARM64. The local build used Zig 0.17.1. `tools/verify_native.py` checks binary freshness; `--wheel` also checks packaged DLLs, manifest and Python module contents.

## Changes

- Removed targeted Chromium/Electron `SetFocus()` fallback. A target lacking a writable interface is refused, rather than silently writing to another element or changing keyboard focus.
- Added `LegacyIAccessible.SetValue` and readback when the normal ValuePattern is missing. Native non-edit hosts can also use their accessibility setter.
- Fixed native append: `EM_SETSEL(-1, -1)` clears a selection; it does not seek to the end of a document. The code now queries the actual length first.
- Preserve native selection/caret offsets after writing, using full 32-bit offsets. Do not restore an old selection if genuine user input changed meanwhile. Yield when the user is actively editing that field.
- Held keys/modifiers and side mouse buttons now count as input. A failed last-input query is treated as busy.
- Validate a drag's points before sending any messages. On failure, release only a drag actually started, at its last delivered point.
- Recognize the modern Paint/XAML canvas as unsupported by the background-message backend. Report `did not drag` instead of implying a successful drawing.
- Static tests now reject focus-setting, pointer/touch injection, input blocking and cursor clipping APIs as well as the previously prohibited global keyboard/mouse APIs.

## All known limits reviewed

| README limit | Investigation and disposition |
| --- | --- |
| Minimized browser/Electron page hidden | Retained. The page's accessibility provider can stop exposing content while minimized. Existing browser startup already uses `behind` and accessibility flags. Existing user windows are not automatically restored or closed. |
| Word/Excel COM; busy cells; unnamed-file save | Retained. Word uses its independent bookmark and range; Excel uses explicit cell addresses. Busy COM calls yield/retry with a bound. Forcing an edited cell to commit or opening Save As would interfere with user state. Word's 34 simulated adapter checks passed; real Office was not exercised. |
| App-defined shortcuts and modern menus | Retained. Message keys and commands are different capabilities. Menus that only exist after opening are not forced open over the user's active menu. The audit does not enable hidden-desktop menu operations on the visible desktop. |
| Elevated windows inaccessible | Retained. Windows integrity boundaries cannot be removed by a normal UI Automation client. No automatic elevation or permission bypass was added. |
| Store/XAML ignores mouse messages; modern Paint canvas | Buttons keep existing accessibility routes. Real Paint 11.2609.401.0 ignored the tested rectangle: **zero changed canvas pixels**, with unchanged final foreground and mouse position. Background drawing remains unsupported and is explicitly refused. A real Win32 test canvas does draw. |
| Store apps ignore start-minimized | Improved by the pre-activation guard; see follow-up above. The earlier one-activation Notepad result motivated the fix. Modern Notepad/Paint subsequently recorded zero measured activations. GUI/broker/architecture limitations remain. |
| Chromium accessibility must be enabled | Existing `open_app` already adds `--force-renderer-accessibility` and `--disable-backgrounding-occluded-windows` for recognized browsers. Already-running apps and custom Electron hosts may not accept a new startup flag. Writable accessibility fields now have a Legacy fallback; unsafe focus fallback was removed. No user's browser profile was restarted. |
| No accessibility tree, OCR/image fallback | Retained. OCR can identify pixels but does not create a writable control or drawing API. Coordinates require visual verification; recognized unsupported Paint canvases refuse. |
| Minimized window cannot be clicked/scrolled/imaged by coordinates | Retained. No automatic restore is added: showing the user's minimized window changes their workspace. Text/native accessibility routes remain available where the provider supports them. |
| Store UWP cannot use hidden desktop | Platform limit retained. No hidden desktop was used in this investigation, as requested. |
| Modern Notepad does not accept blind `WM_CHAR` | Addressed through its actual native RichEdit editor; now improved further with independent TOM ranges. Notepad 11.2607.14.0 preserved selection, Unicode and line breaks and had zero measured activations in the follow-up. A separate test covers fallback native offsets over 65535. Unknown targets are refused. |
| Apps can activate themselves at launch | Pre-activation veto added and verified in the controlled app, Notepad and Paint. Coverage is explicitly bounded as described above; no universal background-input guarantee is claimed. Keeping z-order and keyboard focus are tested separately. |

## Verification and practical limits

- `tests/test_modern_input.py`: deterministic capability/safety tests; simulated controls, not a claim about all apps.
- `tools/visible_input_demo.py`: real visible Win32 edits and painted canvas; exact resulting text, preserved selections, painted pixels, foreground comparison, and read-only WinEvent focus/foreground observers. Its user field is simulated; this is not a sustained human typing stress test.
- `tools/probe_modern_input.py`: real disposable documents in installed Notepad and Paint. These ordinary visible windows can be covered by the user; they are not permanently topmost. Final foreground/mouse snapshots and WinEvent counts are reported separately. Snapshots alone do not prove no transient focus changes.
- Legacy setter fallback is covered with simulated providers. It has not been validated against every WPF/WinUI/Electron editor. A read/modify/write value setter cannot provide atomic isolation from simultaneous human edits in the same field; busy-field checks reduce conflicts but are not a transaction lock.
- Runtime artifacts are local under `.goldentokens/`; they are not intended for publication.

The installed package is editable. Newly launched MCP server processes load these changes; an already-running server needs reconnecting to replace its imported modules. Claude's permission hooks and Codex's registration remain separate.

## API references

- [UI Automation SetFocus changes keyboard focus](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-setfocus).
- [LegacyIAccessible.SetValue is supported only by some elements](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-setvalue).
- [Pointer messages refer to system-maintained pointer information](https://learn.microsoft.com/en-us/windows/win32/inputmsg/wm-pointerdown). Posting a made-up pointer ID is not a valid replacement for input injection.
- [CBT hooks can veto activation/focus before the change](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/legacy/ms644977(v=vs.85)); [mouse activation is identified by Windows](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-cbtactivatestruct).
- [RichEdit SetText edits a range instead of typing at the selection](https://learn.microsoft.com/en-us/windows/win32/api/tom/nf-tom-itextrange-settext).
