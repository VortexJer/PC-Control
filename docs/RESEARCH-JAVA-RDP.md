# Java, Remote Desktop and "any app": research, experiments and decisions (0.9)

A Reddit user wrote: *"It's great for apps that work. For Oracle that run in Java applet and Remote desktop, it still runs into some issues."*
On top of that, 1250 real PC-Control calls from the owner's own Claude Code sessions were mined for failures **in use** (not test
failures): wrong windows, ambiguous names, false "ok" clicks, wrong parameter names, programs not found, loops of the same failure.
This page records what was researched, what was measured on this machine, and what was decided. Every decision keeps the 4 goals:
**no focus stealing, precision, never the user's keyboard or mouse, few tokens and no loops.**

## 1. What the real sessions showed (1250 calls, 175 transcripts; counts measured by the mining script)

| Failure in use | Seen | Fix in 0.9 |
|---|---|---|
| `"Word"` matched Claude's own terminal tab *«◐ Word con PC control»*; `"Spotify"` matched a terminal; `"winword"` found nothing | 18 ambiguous or not-found windows | windows are scored: exact title > the app's **process** > title text; terminals that only *mention* the app and console windows of an app rank lower; process names work |
| A start-up window replaced by the main one → bare *"no window with hwnd"* | 10 | the replacement (same program, one window) is named in the answer; reading follows it by itself |
| Two elements with one name that are ONE control (a list entry and its button) | 3 | pressed automatically (the most precise one); real ambiguity lists container and position |
| `key(keys=…)`, `click(id=…)`, `open_app(app=…)`, drag points as JSON | 10 | aliases mapped before validation (zero tokens added to the tool definitions) |
| `open_app("winword")`, `"excel.exe /e"`, `"chrome …"` → FileNotFoundError | 8 (with 1 elevation error) | PATH → App Paths → Start-menu shortcuts → Store apps, with suggestions |
| Raw Windows errors (1400, 1447, 740) and a ZeroDivisionError in `look` | 5 | clear sentences; zero-size windows answered |
| Clicks that said "ok" but did nothing (canvas, Electron, OCR windows) | 28 "no visible effect" | **pixel verification**: before/after capture, located at the element |
| The same failing call repeated | 15 consecutive same-failure pairs (and 44 identical consecutive calls, many of them harmless) | **loop guard**: 2nd identical failure says what to change; further identical calls refused without acting until the window changes |

## 2. Java (Swing, AWT, Oracle Forms, applets, Web Start)

**Why UI Automation fails:** a Java window (`SunAwtFrame`) is one empty frame for UIA. Screen readers use the **Java Access Bridge (JAB)**.

Sources:
- Oracle: [Using a screen reader and the Java Access Bridge with Oracle Forms](https://docs.oracle.com/en/middleware/fusion-middleware/14.1.2/acfmw/using-a-screen-reader-and-java-access-bridge-with-oracle-forms.html) - JAB is off by default; Forms is accessible through it.
- Robocorp: [java-access-bridge-wrapper](https://pypi.org/project/java-access-bridge-wrapper/) (Python, ctypes on `WindowsAccessBridge-64.dll`; needs a message pump and one thread).
- NVDA's JAB support ([nvaccess/nvda](https://github.com/nvaccess/nvda), `JABHandler`): the reference screen-reader client (read, not copied).
- Test tools for Oracle Forms: [Squish](https://qatools.knowledgebase.qt.io/squish/java/howto/automating-oracle-forms-applications/), [Microsoft IA-Connect Java](https://learn.microsoft.com/en-us/connectors/iaconnectjava) - all go through JAB.
- RPA forums ([Blue Prism](https://community.blueprism.com/t5/Product-Forum/Oracle-forms/m-p/50808), [UiPath](https://forum.uipath.com/t/issues-with-java-checkbox-oracle-forms/5730137)): when JAB is not on, they fall back to "surface" (image) automation.

Decisions (all measured with a real Swing/AWT app, `tests/java_app`, on Temurin 21):
- Own ctypes binding (`pc_control/java.py`), no new dependency. One dedicated thread with a message loop runs every bridge call.
- Actions are the element's **own** accessible actions (`doAccessibleActions`, = `doClick`), text is `setTextContents`, selections go through
  `AccessibleSelection`. None of these move the mouse, press keys or need focus. A menu entry of a **closed** menu can be pressed.
- An action that opens a **modal dialog** blocks inside Java until the dialog closes: actions run on a disposable thread with a 2.5 s
  deadline and are reported as "delivered, the app is busy" instead of freezing.
- Java reports positions in its own scaled pixels: converted with the frame's measured scale (HiDPI).
- Keys on a Java field are the field's editor actions: Enter = `notify-field-accept`, Backspace = `delete-previous`...
- Turning the bridge on: apps that PC-Control opens get it **for that process only** (`JAVA_TOOL_OPTIONS=-Djavax.accessibility.assistive_technologies=…`);
  for apps the user starts (an Oracle Forms shortcut), `pc-control java enable` adds one line to `~/.accessibility.properties`
  (what `jabswitch -enable` does), keeping foreign lines. With the bridge off, `look` says exactly that and reads pixels meanwhile.
- Swing's own combo-box accessibility clears the selection before setting the new one (`setSelectedIndex(-1)` then `i`): the app sees a
  transient "no selection" event. A screen reader does the same; it is documented, not hidden.

## 3. The launch focus hole (found while testing Java)

Measured with a 1 ms foreground poll: when a Java app shows its frame it calls `SetForegroundWindow`; Windows first **empties** the
foreground and then activates the frame. The native guard allowed the second step because an empty foreground counted as permission
(`ACT NOFG ALLOW` recorded inside the app). Fix: an empty foreground is no longer permission (`native/focus_guard.c`), and while the
foreground is empty the window the user had gets it back (`FocusGuard.hand_back`, the ONE allowed `SetForegroundWindow`, only when nobody
owns the foreground, only to the user's own window, never while they hold a mouse button). Measured: the app never holds the foreground;
the empty gap lasts ~50 ms. Raymond Chen, [You can't simulate keyboard input with PostMessage](https://devblogs.microsoft.com/oldnewthing/20250319-00/?p=110979),
explains why posted keys are second-class; it also mentions that a fake WM_SETFOCUS fools some programs - the key to the next section.

## 4. Remote Desktop (mstsc) and other remote screens

Sources:
- [IMsRdpClientNonScriptable::SendKeys](https://msdn.microsoft.com/en-us/library/aa381294(v=vs.85).aspx): official, keys only, and only for
  a client control **hosted by our own process** - not for the user's mstsc.
- [IMsRdpInputSink](https://learn.microsoft.com/en-gb/windows/win32/termserv/imsrdpinputsink) (MsTscAx, Windows 8.1+): mouse and keyboard
  for a hosted control, but not in the type library and its method order / enums are undocumented: calling it blind could crash. Rejected.
- AutoHotkey/AutoIt forums ([1](https://www.autohotkey.com/boards/viewtopic.php?p=380794), [2](https://autoitscript.com/forum/topic/190779-issues-with-controlclick-and-remote-desktop-sessions)):
  ControlClick/ControlSend into RDP is unreliable. [TestComplete](https://community.smartbear.com/t5/TestComplete-Questions/TestComplete-not-recognising-Remote-Desktop-window-objects/m-p/205515): "everything within RDP window is just one big image".
- [aardwolf](https://github.com/skelsec/aardwolf): a headless Python RDP client (keyboard, mouse, framebuffer). Rejected for now: it opens a
  SECOND session, and Windows' default "one session per user" would disconnect the user's own mstsc - the opposite of not interfering -
  and it is a security-tool dependency.

Experiments (xrdp in an isolated WSL distro, `tools/rdp_testenv.py`; mstsc of Windows 11):
- The session window (`TscShellContainerClass`) is captured while **covered** (`PrintWindow` full content) and OCR reads it.
- Mouse and key messages posted to the client's `IHWindowClass` ("Input Capture Window") are **ignored** while the client believes it is
  inactive. Telling it - `WM_ACTIVATE`/`WM_SETFOCUS` **messages**, which change nothing in Windows' real focus, foreground or z-order -
  makes it forward posted mouse messages and **scancode** key messages into the session. Afterwards it is told it lost the focus.
- `WM_CHAR` is ignored: text is typed as key presses of the local layout (`VkKeyScan`, AltGr, and dead keys via `ToUnicodeEx` for á, ü...).
- Windows 11 asks before opening an unsigned `.rdp` file ("I understand and allow...") - a security choice PC-Control never ticks for the
  user. `mstsc /v:host` does not ask that. The server-certificate prompt is an ordinary dialog the user (or Claude, when allowed) answers.
- Default mstsc only hooks the keyboard in full screen ("Apply Windows key combinations: full screen only"); input is sent only while the
  user is idle, in bursts of a fraction of a second.

**Best precision for remote apps** (e.g. Oracle Forms on a terminal server): run PC-Control *inside* the remote session; there the Java
app is read through its bridge exactly as above. From the local machine the remote screen is pixels: reading is OCR/image, acting is
input into the session, and every action is verified by pixels.

## 5. Tools borrowed from other fields

- **Accessibility (screen readers):** JAB, UI Automation patterns, WinEvent hooks - exact elements without the mouse.
- **IME / input-method world:** thread-scoped CBT hooks that veto activation *before* it happens (`native/focus_guard.c`).
- **QA / visual testing:** before/after pixel comparison, made tolerant to a blinking caret and stray pixels (`core.pixel_change`).
- **OCR:** Windows' own OCR engine (no download, no cloud).
- **Ops:** WSL as a disposable RDP server for real end-to-end tests.

## 5b. Independence, real apps and general support (added 2026-11-21)

- **Shared key state, measured.** `tests/input_probe_app.py` logs what an app's own thread sees. With the "user" holding Shift (or the left
  button) in their foreground window, a BACKGROUND app receiving a posted `WM_KEYDOWN`/`WM_LBUTTONDOWN` saw `GetKeyState(VK_SHIFT)` and
  `GetKeyState(VK_LBUTTON)` as held. Raymond Chen explains why posted input is not real input
  ([You can't simulate keyboard input with PostMessage, revisited](https://devblogs.microsoft.com/oldnewthing/20250319-00/?p=110979)).
  Decision: Claude should not behave like a keyboard at all. Semantic interfaces come first (UI Automation patterns, the Java Access
  Bridge, Scintilla's API, COM). Message input is the last resort, and it waits ONLY while a modifier is held (`core.wait_free_keys`,
  `core.wait_free_mouse`), never for the user to stop typing in another window.
- **Oracle Forms.** Squish documents that Forms UIs are Java applets built on Oracle's lightweight `oracle.ewt.lwAWT` components, with an
  invisible `GlassMouseGrabProvider` overlay over the whole UI that swallows coordinate clicks
  ([Squish: Automating Oracle Forms](https://qatools.knowledgebase.qt.io/squish/java/howto/automating-oracle-forms-applications/)).
  That is why PC-Control acts on Java elements through the bridge's own actions and never by coordinates. Forms is launched as applet,
  object/embed or JNLP ([Oracle Forms 12.2.1.4: Deploying Applications](https://docs.oracle.com/en/middleware/developer-tools/forms/12.2.1.4/working-forms/deploying-applications.html)).
- **Scintilla.** Real Notepad++ (downloaded, not written by us) showed an empty UI Automation tree inside the editor. Scintilla has a
  complete message API ([ScintillaDoc](https://www.scintilla.org/ScintillaDoc.html)); `pc_control/scintilla.py` uses it with a buffer
  allocated in the editor's process. It covers every Scintilla-based app in one place.
- **32-bit apps.** `SetWindowsHookEx` only loads a hook DLL into processes of the same architecture as the caller, so the 64-bit server
  refused 32-bit apps (the portable Notepad++ is x86). A 32-bit helper process now installs the same guard.
- **Classifiers for "any app".** Studied for later: pywinauto's window-class to control-type tables (`windowclasses`, e.g.
  `ThunderRT6TextBox`, `TMemo`, `WindowsForms*`; [pywinauto win32_controls](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.win32_controls.html)),
  NVDA's per-class overlays and display model ([NVDA](https://github.com/nvaccess/nvda)), UIED (classic CV plus a CNN for widgets;
  [paper](https://arxiv.org/abs/2008.05132)) and OmniParser (a YOLO detector of interactable icons; [OmniParser](https://github.com/microsoft/OmniParser)).
  The detectors would only find WHERE things are; acting still needs a semantic route, so they are not shipped now.

## 6. Limits that remain

- Java apps started by the user with the bridge off are read from pixels until the user runs `pc-control java enable` and restarts them.
- Swing combo selection emits a transient "no selection" event (Swing's own accessibility does that).
- On a remote screen, only what OCR/image shows is known; elements have no identity, so the pixel check is the proof.
- Remote clients other than mstsc (Citrix, VMware, VNC, VirtualBox...) get the same route (input window, activation notifications,
  pixel verification), tested so far only with mstsc; a client that ignores it is reported as "no visible effect", never as done.
- The hidden desktop remains, as a last resort only.
