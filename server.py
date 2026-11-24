#!/usr/bin/env python3
"""
Phone Trackpad - Mac side.

Serves the trackpad web app to your iPhone and moves the Mac cursor
using macOS CoreGraphics. No installs needed: only the Python that ships
with macOS (3.8+).

Run:  python3 server.py
Then open the printed URL on your iPhone (same Wi-Fi).
"""
import base64
import ctypes
import hashlib
import json
import os
import secrets
import socket
import socketserver
import struct
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))


def load_token():
    """Keep the same secret between runs so the Home Screen icon keeps working."""
    path = os.path.join(HERE, ".token")
    try:
        with open(path) as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(6)
    with open(path, "w") as f:
        f.write(tok)
    return tok


TOKEN = os.environ.get("TOKEN") or load_token()

# --------------------------------------------------------------------------
# CoreGraphics bindings (ctypes, no pip packages)
# --------------------------------------------------------------------------
class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


cg = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
cf = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

vp, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
cg.CGEventCreate.restype = vp
cg.CGEventCreate.argtypes = [vp]
cg.CGEventGetLocation.restype = CGPoint
cg.CGEventGetLocation.argtypes = [vp]
cg.CGEventCreateMouseEvent.restype = vp
cg.CGEventCreateMouseEvent.argtypes = [vp, u32, CGPoint, u32]
cg.CGEventCreateScrollWheelEvent2.restype = vp
cg.CGEventCreateScrollWheelEvent2.argtypes = [vp, u32, u32, i32, i32, i32]
cg.CGEventSetIntegerValueField.argtypes = [vp, u32, ctypes.c_int64]
cg.CGEventPost.argtypes = [u32, vp]
cg.CGGetActiveDisplayList.restype = i32
cg.CGGetActiveDisplayList.argtypes = [u32, ctypes.POINTER(u32), ctypes.POINTER(u32)]
cg.CGDisplayBounds.restype = CGRect
cg.CGDisplayBounds.argtypes = [u32]
cg.AXIsProcessTrusted.restype = ctypes.c_bool
cg.CGEventCreateKeyboardEvent.restype = vp
cg.CGEventCreateKeyboardEvent.argtypes = [vp, ctypes.c_uint16, ctypes.c_bool]
cg.CGEventSetFlags.argtypes = [vp, ctypes.c_uint64]
cg.CGEventKeyboardSetUnicodeString.argtypes = [vp, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)]
cf.CFRelease.argtypes = [vp]

# CGEventType / button / field constants
LEFT_DOWN, LEFT_UP, RIGHT_DOWN, RIGHT_UP = 1, 2, 3, 4
MOVED, LEFT_DRAGGED, RIGHT_DRAGGED = 5, 6, 7
BTN_LEFT, BTN_RIGHT = 0, 1
HID_TAP = 0
FIELD_CLICK_STATE = 1
SCROLL_UNIT_PIXEL = 0


def displays():
    ids = (u32 * 16)()
    count = u32(0)
    cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count))
    return [cg.CGDisplayBounds(ids[i]) for i in range(count.value)]


def inside(r, x, y):
    return (r.origin.x <= x < r.origin.x + r.size.width
            and r.origin.y <= y < r.origin.y + r.size.height)


class Mouse:
    def __init__(self):
        self.lock = threading.Lock()
        self.left = False
        self.right = False

    @staticmethod
    def pos():
        e = cg.CGEventCreate(None)
        p = cg.CGEventGetLocation(e)
        cf.CFRelease(e)
        return p

    @staticmethod
    def _post(kind, point, button, clicks=None):
        e = cg.CGEventCreateMouseEvent(None, kind, point, button)
        if clicks:
            cg.CGEventSetIntegerValueField(e, FIELD_CLICK_STATE, clicks)
        cg.CGEventPost(HID_TAP, e)
        cf.CFRelease(e)

    def move(self, dx, dy):
        with self.lock:
            p = self.pos()
            x, y = p.x + dx, p.y + dy
            screens = displays()
            if not any(inside(r, x, y) for r in screens):
                # Keep the cursor on the screen it is currently on.
                home = next((r for r in screens if inside(r, p.x, p.y)), screens[0])
                x = min(max(x, home.origin.x), home.origin.x + home.size.width - 1)
                y = min(max(y, home.origin.y), home.origin.y + home.size.height - 1)
            if self.left:
                kind, btn = LEFT_DRAGGED, BTN_LEFT
            elif self.right:
                kind, btn = RIGHT_DRAGGED, BTN_RIGHT
            else:
                kind, btn = MOVED, BTN_LEFT
            self._post(kind, CGPoint(x, y), btn)

    def button(self, which, down, clicks=1):
        with self.lock:
            p = self.pos()
            if which == "right":
                self.right = down
                self._post(RIGHT_DOWN if down else RIGHT_UP, p, BTN_RIGHT, clicks)
            else:
                self.left = down
                self._post(LEFT_DOWN if down else LEFT_UP, p, BTN_LEFT, clicks)

    def click(self, which, clicks=1):
        self.button(which, True, clicks)
        self.button(which, False, clicks)

    def scroll(self, dx, dy):
        e = cg.CGEventCreateScrollWheelEvent2(
            None, SCROLL_UNIT_PIXEL, 2, int(round(dy)), int(round(dx)), 0)
        cg.CGEventPost(HID_TAP, e)
        cf.CFRelease(e)

    def release_all(self):
        if self.left:
            self.button("left", False)
        if self.right:
            self.button("right", False)


mouse = Mouse()


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------
FLAG = {"shift": 0x20000, "ctrl": 0x40000, "alt": 0x80000, "cmd": 0x100000}
ARROW_FLAGS = 0x800000 | 0x200000  # fn + numeric pad, like a real arrow key
KEY_CMD = 55

# Named keys and US-layout characters (used for shortcuts like Cmd+C).
KEYCODES = {
    "enter": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53,
    "delete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
}
for _ch, _code in zip(
        "asdfhgzxcvbqweryt123465=97-80]ou[ip"
        "lj'k;\\,/nm.`",
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
         21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
         37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 50]):
    KEYCODES[_ch] = _code


class Keyboard:
    def __init__(self):
        self.lock = threading.Lock()
        self.switcher = False

    @staticmethod
    def _key(code, down, flags=0):
        e = cg.CGEventCreateKeyboardEvent(None, code, down)
        cg.CGEventSetFlags(e, flags)
        cg.CGEventPost(HID_TAP, e)
        cf.CFRelease(e)

    def press(self, name, mods=()):
        flags = 0
        if name == " ":
            name = "space"
        if len(name) == 1:
            if name.isupper():
                flags |= FLAG["shift"]
            name = name.lower()
        else:
            name = name.lower()
        code = KEYCODES.get(name)
        if code is None:
            return
        for m in mods:
            flags |= FLAG.get(m, 0)
        if name in ("left", "right", "up", "down"):
            flags |= ARROW_FLAGS
        with self.lock:
            self._key(code, True, flags)
            self._key(code, False, flags)

    def type_text(self, text):
        with self.lock:
            for ch in text:
                if ch in "\r\n":
                    self._key(KEYCODES["enter"], True)
                    self._key(KEYCODES["enter"], False)
                    continue
                units = ch.encode("utf-16-le")
                buf = (ctypes.c_uint16 * (len(units) // 2)).from_buffer_copy(units)
                for down in (True, False):
                    e = cg.CGEventCreateKeyboardEvent(None, 0, down)
                    cg.CGEventSetFlags(e, 0)
                    cg.CGEventKeyboardSetUnicodeString(e, len(buf), buf)
                    cg.CGEventPost(HID_TAP, e)
                    cf.CFRelease(e)

    # Cmd+Tab app switcher, held open while three fingers are down.
    def switch(self, action):
        cmd, tab = FLAG["cmd"], KEYCODES["tab"]
        with self.lock:
            if action == "start" and not self.switcher:
                self.switcher = True
                self._key(KEY_CMD, True, cmd)
                self._key(tab, True, cmd)
                self._key(tab, False, cmd)
            elif action in ("next", "prev") and self.switcher:
                flags = cmd | (FLAG["shift"] if action == "prev" else 0)
                self._key(tab, True, flags)
                self._key(tab, False, flags)
            elif action == "end" and self.switcher:
                self.switcher = False
                self._key(KEY_CMD, False, 0)

    def release_all(self):
        self.switch("end")


keyboard = Keyboard()


def handle_message(msg):
    t = msg.get("t")
    if t == "text":
        keyboard.type_text(str(msg.get("s", ""))[:500])
    elif t == "key":
        keyboard.press(str(msg.get("k", "")), msg.get("mods") or [])
    elif t == "switcher":
        keyboard.switch(msg.get("a"))
    elif t == "mission":
        # Ctrl+Up = Mission Control, Ctrl+Down = App windows
        keyboard.press("up" if msg.get("d") == "up" else "down", ["ctrl"])
    elif t == "move":
        mouse.move(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
    elif t == "scroll":
        mouse.scroll(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
    elif t == "click":
        mouse.click(msg.get("b", "left"), int(msg.get("n", 1)))
    elif t == "down":
        mouse.button(msg.get("b", "left"), True, int(msg.get("n", 1)))
    elif t == "up":
        mouse.button(msg.get("b", "left"), False, int(msg.get("n", 1)))


# --------------------------------------------------------------------------
# Tiny HTTP + WebSocket server (stdlib only)
# --------------------------------------------------------------------------
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(4096).decode("latin-1").strip()
            if not line:
                return
            method, target, _ = line.split(" ", 2)
            headers = {}
            while True:
                h = self.rfile.readline(4096).decode("latin-1").strip()
                if not h:
                    break
                k, _, v = h.partition(":")
                headers[k.strip().lower()] = v.strip()

            url = urlparse(target)
            # The Home Screen icon is not secret; iOS fetches it without the token.
            if url.path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
                with open(os.path.join(HERE, "apple-touch-icon.png"), "rb") as f:
                    return self.respond(200, "image/png", f.read())

            token = parse_qs(url.query).get("t", [""])[0]
            if not secrets.compare_digest(token, TOKEN):
                return self.respond(403, "text/plain", b"Wrong or missing token. "
                                    b"Use the full URL printed on your Mac.")

            if url.path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                return self.websocket(headers)
            if url.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self.respond(200, "text/html; charset=utf-8", f.read())
            self.respond(404, "text/plain", b"Not found")
        except (ConnectionError, ValueError):
            pass

    def respond(self, code, ctype, body):
        reason = {200: "OK", 403: "Forbidden", 404: "Not Found"}[code]
        self.wfile.write(
            f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
            f"Connection: close\r\n\r\n".encode() + body)

    # -- WebSocket ---------------------------------------------------------
    def websocket(self, headers):
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.wfile.write(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        peer = self.client_address[0]
        print(f"  phone connected: {peer}")
        try:
            while True:
                opcode, payload = self.read_frame()
                if opcode == 0x8:  # close
                    self.send_frame(0x8, b"")
                    break
                if opcode == 0x9:  # ping
                    self.send_frame(0xA, payload)
                    continue
                if opcode == 0x1:
                    try:
                        handle_message(json.loads(payload.decode("utf-8")))
                    except (ValueError, TypeError):
                        pass
        except (ConnectionError, struct.error, OSError):
            pass
        finally:
            mouse.release_all()  # never leave a button or Cmd stuck down
            keyboard.release_all()
            print(f"  phone disconnected: {peer}")

    def read_exact(self, n):
        data = self.rfile.read(n)
        if len(data) < n:
            raise ConnectionError("socket closed")
        return data

    def read_frame(self):
        b1, b2 = self.read_exact(2)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.read_exact(8))[0]
        if length > 1 << 16:
            raise ConnectionError("frame too large")
        mask = self.read_exact(4) if b2 & 0x80 else b"\0\0\0\0"
        data = self.read_exact(length)
        return opcode, bytes(c ^ mask[i % 4] for i, c in enumerate(data))

    def send_frame(self, opcode, payload):
        self.wfile.write(bytes([0x80 | opcode, len(payload)]) + payload)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def local_hostname():
    """The Mac's Bonjour name (e.g. my-mac.local). Unlike the IP address,
    it stays the same on every Wi-Fi network."""
    try:
        out = subprocess.run(["scutil", "--get", "LocalHostName"],
                             capture_output=True, text=True, timeout=3)
        name = out.stdout.strip()
        return f"{name}.local" if out.returncode == 0 and name else None
    except (OSError, subprocess.SubprocessError):
        return None


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ask_for_accessibility():
    """Show macOS's 'allow this app to control your computer' prompt and add
    this program to the Accessibility list, so the user only flips a switch."""
    try:
        cf.CFDictionaryCreate.restype = vp
        cf.CFDictionaryCreate.argtypes = [vp, ctypes.POINTER(vp), ctypes.POINTER(vp),
                                          ctypes.c_long, vp, vp]
        cg.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        cg.AXIsProcessTrustedWithOptions.argtypes = [vp]
        key = vp.in_dll(cg, "kAXTrustedCheckOptionPrompt")
        true = vp.in_dll(cf, "kCFBooleanTrue")
        keys, values = (vp * 1)(key.value), (vp * 1)(true.value)
        opts = cf.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.addressof(vp.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
            ctypes.addressof(vp.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
        cg.AXIsProcessTrustedWithOptions(opts)
        cf.CFRelease(opts)
    except (AttributeError, ValueError, OSError):
        pass


def main():
    if not cg.AXIsProcessTrusted():
        ask_for_accessibility()
        print("\n  ! This app can't control the cursor yet.")
        print("    Open System Settings > Privacy & Security > Accessibility and turn ON:")
        if sys.stdin is not None and sys.stdin.isatty():
            print("      the app you ran this from (Terminal, iTerm, VS Code...)")
        else:
            print(f"      python3  ({os.path.realpath(sys.executable)})")
        print("    then restart the server.\n")
    name = local_hostname()
    print("\n  Phone Trackpad is running.")
    print("  On your iPhone (same Wi-Fi), open:\n")
    if name:
        print(f"      http://{name}:{PORT}/?t={TOKEN}\n")
        print("  This address keeps working when you change Wi-Fi networks.")
        print("  If a network blocks it, use the IP address for that network instead:")
        print(f"      http://{local_ip()}:{PORT}/?t={TOKEN}   (current network only)\n")
    else:
        print(f"      http://{local_ip()}:{PORT}/?t={TOKEN}\n")
    print("  Tip: Safari > Share > Add to Home Screen to use it like an app.")
    print("  Press Ctrl+C to stop.\n")
    with Server(("0.0.0.0", PORT), Handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n  Stopped.")
            sys.exit(0)


if __name__ == "__main__":
    main()
