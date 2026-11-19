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

Speed, scroll speed and scroll direction are in the settings button (top right).

## Notes
- The URL has a secret (`?t=…`, saved in `.token`) so nobody else on the Wi-Fi can control your Mac. Delete `.token` to make a new one.
- If your Mac's IP changes, the Home Screen icon stops working; open the new printed URL and add it again.
