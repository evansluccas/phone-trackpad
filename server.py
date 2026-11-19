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


def handle_message(msg):
    t = msg.get("t")
    if t == "move":
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
            mouse.release_all()  # never leave a button stuck down
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


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    if not cg.AXIsProcessTrusted():
        print("\n  ! This app can't control the cursor yet.")
        print("    Open System Settings > Privacy & Security > Accessibility,")
        print("    turn ON the app you ran this from (Terminal, iTerm, VS Code...),")
        print("    then restart this script.\n")
    url = f"http://{local_ip()}:{PORT}/?t={TOKEN}"
    print("\n  Phone Trackpad is running.")
    print("  On your iPhone (same Wi-Fi), open:\n")
    print(f"      {url}\n")
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
