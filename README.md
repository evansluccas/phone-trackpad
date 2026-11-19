# Phone Trackpad

Use your iPhone as a trackpad for your Mac. No App Store, no installs.

## Start
1. On the Mac: `python3 server.py`
2. First time only: System Settings → Privacy & Security → **Accessibility** → turn ON the app you ran it from (Terminal, iTerm, VS Code…), then run the script again. If macOS asks to allow incoming connections for Python, click Allow.
3. On the iPhone (same Wi-Fi): open the URL the script prints.
4. Optional: Safari → Share → **Add to Home Screen** so it opens full-screen like an app.

## Gestures
| Gesture | Does |
|---|---|
| 1 finger slide | Move cursor |
| Tap | Click |
| Double-tap | Double-click |
| Double-tap, then slide | Drag |
| 2 fingers slide | Scroll |
| 2-finger tap | Right-click |
| Hold **Left** + slide on pad | Drag |
| 3 fingers left / right | Move between desktops and full-screen apps |
| 3 fingers up / down | Mission Control / app windows |

## Keyboard
Tap the keyboard button in the middle to type on your Mac. A row of extra keys appears: esc, tab, arrows, and ⌘ ⌥ ⌃ ⇧. Tap a modifier, then a letter, for shortcuts like ⌘C or ⌘V.

## Slides mode
Switch to **Slides** at the top. Left and Right become big ‹ Back / Next › buttons that send the arrow keys, so you can drive Keynote, PowerPoint or Google Slides from your phone.

Speed, scroll speed and scroll direction are in the settings button (top right).

3-finger swipes use the standard Mission Control shortcuts (⌃← / ⌃→ for desktops, ⌃↑ / ⌃↓ for Mission Control and app windows). If you turned those off in System Settings → Keyboard → Keyboard Shortcuts → Mission Control, the swipes won't do anything.

## Notes
- The URL has a secret (`?t=…`, saved in `.token`) so nobody else on the Wi-Fi can control your Mac. Delete `.token` to make a new one.
- If your Mac's IP changes, the Home Screen icon stops working; open the new printed URL and add it again.
