# pcsight

Let an AI see and operate Windows apps cheaply, **without stealing focus, moving your mouse or typing on your keyboard**.
An MCP server (works with Claude Code and any MCP client). Early version, v0.1.

## What it does

- **Sees a window without focusing it.** `look` picks the cheapest of: UI-tree text, OCR text, or an image with numbered marks.
  It never sends empty text, text that costs more than the image, or text that does not cover the window.
- **Acts on a window without focusing it.** Clicks and typing go straight to the control (`BM_CLICK`, `EM_REPLACESEL`, ...).
  Nothing here calls `SetForegroundWindow`, `SendInput`, `keybd_event` or `SetCursorPos` (a test scans the code for them).
- **Answers with only what changed** after each action, not the whole screen again.
- **Opens apps out of your way.** `open_app` starts the app minimized (nothing on screen, no focus, its button in the
  taskbar so you can open it with one click), or on a hidden desktop with `hidden=true`.

## Safety model

| Rule | How |
|---|---|
| Reading is limited | Password managers, terminals, registry tools are on a deny-list. |
| Acting is opt-in | `click`, `type`, `key`, `open_app` only work on apps you allow: `PCSIGHT_ALLOW=notepad,winword` or `~/.pcsight/allow.txt`. |
| Kill switch | Create `~/.pcsight/PAUSE` (or set `PCSIGHT_PAUSE=1`): every tool refuses. |
| No blind typing | `type` needs an element id from `look`. |
| Never in your way | New dialogs in your own apps are only sent behind other windows, and never if you are using them. |

## Install

```
pip install -r requirements.txt
python -m pcsight.server        # stdio MCP server
```

Windows 10/11 only. OCR uses the OCR engine built into Windows (fast, 0.2-0.8 s); a Tesseract fallback is planned.

## Tools

`windows`, `look`, `click`, `type`, `key`, `open_app`.

## Tests

Tests never open anything on your screen: they use a throw-away window on a hidden desktop.

```
python tests/test_decide.py       # cost/quality rules
python tests/test_static.py       # no intrusive API calls in the package
python tests/test_hidden.py       # app on the hidden desktop: never on your screen
python tests/test_background.py   # clicks and typing without focus
```

## Known limits

- Single keys only; shortcuts (`ctrl+s`) would need focus, so they are not supported.
- Apps without an accessibility tree rely on OCR; apps that draw everything themselves (some games) may need images.
- Store (UWP) apps cannot run on a hidden desktop.
- Background typing through `WM_CHAR` is not accepted by every app (the modern Notepad ignores it); use element ids.

## License

MIT
