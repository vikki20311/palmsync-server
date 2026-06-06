#!/usr/bin/env python3
"""
Palmsync – Gesture Controlled Secure File Transfer (LAN Only)
Final Year Project Demo – Complete Version with Progress Bar & Receiver Confirmation
"""

import os
import sys
import time
import socket
import threading
import queue
import psutil
import struct
import tempfile
from datetime import datetime
from pathlib import Path

# GUI
import tkinter as tk
from tkinter import ttk

# Computer vision
import cv2
import mediapipe as mp

# Windows specific
import win32gui
import win32process
import win32con

# Keyboard simulation for URL grabbing
from pynput.keyboard import Key, Controller as KeyboardController

# ========== CONFIGURATION ==========
DISCOVERY_PORT = 9999
FILE_TRANSFER_PORT = 8888
BROADCAST_TIMEOUT = 2.0
GESTURE_COOLDOWN = 0.8
AUTO_SEND_DELAY = 5.0
GESTURE_CONFIRM_FRAMES = 5
INCOMING_TIMEOUT = 10.0

# ========== GLOBAL STATE ==========
class State:
    def __init__(self):
        self.running = True
        self.current_gesture = "none"
        self.discovered_peers = []
        self.selected_peer_index = 0
        self.detected_content = None
        self.send_queue = queue.Queue()
        self.receive_queue = queue.Queue()
        self.popup_active = False
        self.gesture_counter = 0
        self.last_gesture_time = 0
        # For receiving
        self.incoming_file = None
        self.incoming_popup = None
        self.incoming_conn = None

state = State()

# ========== CONTENT DETECTION ==========
def get_active_window_info():
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32process.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
        exe_path = win32process.GetModuleFileNameEx(handle, 0)
        process_name = os.path.basename(exe_path).lower()
        return hwnd, title, process_name
    except:
        return None, "", ""

def get_file_path_from_active_window():
    try:
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)
        cmdline = process.cmdline()
        if len(cmdline) > 1:
            for arg in cmdline[1:]:
                if arg.startswith(('http://', 'https://', '-', '--')):
                    continue
                if os.path.exists(arg) and os.path.isfile(arg):
                    return arg
        return None
    except:
        return None

def detect_url_from_browser(title, process_name):
    if process_name not in ['chrome.exe', 'msedge.exe', 'firefox.exe']:
        return None
    keyboard = KeyboardController()
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        original = root.clipboard_get()
    except:
        original = ""
    hwnd = win32gui.GetForegroundWindow()
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    keyboard.press(Key.ctrl)
    keyboard.press('l')
    keyboard.release('l')
    keyboard.release(Key.ctrl)
    time.sleep(0.1)
    keyboard.press(Key.ctrl)
    keyboard.press('c')
    keyboard.release('c')
    keyboard.release(Key.ctrl)
    time.sleep(0.1)
    try:
        url = root.clipboard_get()
    except:
        url = ""
    if original:
        root.clipboard_clear()
        root.clipboard_append(original)
    root.destroy()
    if url.startswith(('http://', 'https://')):
        return url
    return None

def detect_content():
    filepath = get_file_path_from_active_window()
    if filepath:
        return {'type': 'file', 'path': filepath, 'title': os.path.basename(filepath)}
    hwnd, title, proc = get_active_window_info()
    url = detect_url_from_browser(title, proc)
    if url:
        return {'type': 'url', 'path': url, 'title': title.split(' - ')[0] if ' - ' in title else title}
    screenshot = take_screenshot()
    return {'type': 'screenshot', 'path': screenshot, 'title': 'Screenshot ' + datetime.now().strftime('%H-%M-%S')}

def take_screenshot():
    from PIL import ImageGrab
    im = ImageGrab.grab()
    temp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    im.save(temp.name)
    return temp.name

# ========== LAN DISCOVERY ==========
def broadcast_discovery():
    peers = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(BROADCAST_TIMEOUT)
        msg = b"PALMSYNC_DISCOVERY"
        sock.sendto(msg, ('255.255.255.255', DISCOVERY_PORT))
        start = time.time()
        while time.time() - start < BROADCAST_TIMEOUT:
            try:
                data, addr = sock.recvfrom(1024)
                if data.startswith(b"PALMSYNC_REPLY"):
                    ip = addr[0]
                    if ip != get_local_ip():
                        peers.append(ip)
            except socket.timeout:
                pass
        sock.close()
    except:
        pass
    if not peers:
        print("[DISCOVERY] Broadcast failed, scanning subnet...")
        peers = scan_subnet()
    peers = list(set(peers))
    return peers

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except:
        ip = '127.0.0.1'
    s.close()
    return ip

def scan_subnet():
    local_ip = get_local_ip()
    if local_ip == '127.0.0.1':
        return []
    parts = local_ip.split('.')
    base = '.'.join(parts[:-1])
    peers = []
    for i in range(1, 255):
        ip = f"{base}.{i}"
        if ip == local_ip:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.05)
        result = sock.connect_ex((ip, DISCOVERY_PORT))
        if result == 0:
            peers.append(ip)
        sock.close()
    return peers

def start_discovery_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('', DISCOVERY_PORT))
    except:
        print("[DISCOVERY] Could not bind discovery port. Using fallback.")
        return
    sock.settimeout(1.0)
    while state.running:
        try:
            data, addr = sock.recvfrom(1024)
            if data.startswith(b"PALMSYNC_DISCOVERY"):
                reply = b"PALMSYNC_REPLY"
                sock.sendto(reply, addr)
        except socket.timeout:
            continue
    sock.close()

# ========== TCP FILE TRANSFER (with receiving confirmation) ==========
def send_file(peer_ip, file_path, progress_callback=None):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect((peer_ip, FILE_TRANSFER_PORT))
        
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        header = f"{file_name}|{file_size}".encode()
        sock.send(struct.pack('>I', len(header)))
        sock.send(header)
        
        # Wait for receiver's response
        response = sock.recv(4)
        if response == b'REJ':
            sock.close()
            return False, "Receiver rejected"
        elif response != b'ACC':
            sock.close()
            return False, "Unexpected response from receiver"
        
        # Receiver accepted – send file
        sent = 0
        start_time = time.time()
        with open(file_path, 'rb') as f:
            while sent < file_size:
                chunk = f.read(8192)
                if not chunk:
                    break
                sock.send(chunk)
                sent += len(chunk)
                if progress_callback:
                    progress = (sent / file_size) * 100
                    elapsed = time.time() - start_time
                    speed = sent / elapsed / (1024 * 1024) if elapsed > 0 else 0
                    progress_callback(progress, speed)
        sock.close()
        return True, "Sent successfully!"
    except socket.timeout:
        return False, "Timeout – receiver did not respond"
    except Exception as e:
        return False, f"Error: {e}"

def start_file_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('', FILE_TRANSFER_PORT))
    except:
        print("[FILE SERVER] Port already in use. Exiting.")
        return
    sock.listen(5)
    while state.running:
        try:
            conn, addr = sock.accept()
            threading.Thread(target=handle_incoming_request, args=(conn, addr), daemon=True).start()
        except:
            pass
    sock.close()

def handle_incoming_request(conn, addr):
    try:
        raw_len = conn.recv(4)
        if not raw_len:
            conn.close()
            return
        header_len = struct.unpack('>I', raw_len)[0]
        header = conn.recv(header_len).decode()
        file_name, file_size_str = header.split('|')
        file_size = int(file_size_str)
        
        state.incoming_conn = conn
        state.incoming_file = {'name': file_name, 'size': file_size, 'sender': addr[0]}
        
        # Signal main thread to show incoming popup
        state.receive_queue.put(('show_incoming', {
            'name': file_name,
            'size': file_size,
            'sender': addr[0],
            'conn': conn
        }))
    except Exception as e:
        print(f"[RECEIVE] Error: {e}")
        conn.close()

# ========== GESTURE DETECTION ==========
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.3,
    min_tracking_confidence=0.3
)

def get_finger_states(landmarks):
    tips = [4, 8, 12, 16, 20]
    bases = [3, 6, 10, 14, 18]
    fingers = []
    for tip, base in zip(tips, bases):
        if landmarks.landmark[tip].y < landmarks.landmark[base].y:
            fingers.append(1)
        else:
            fingers.append(0)
    return fingers

def detect_gesture(frame):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = hands.process(rgb)
    if not results.multi_hand_landmarks:
        return 'none'
    landmarks = results.multi_hand_landmarks[0]
    fingers = get_finger_states(landmarks)
    if sum(fingers) == 5:
        return 'palm'
    if sum(fingers[1:]) == 0:
        return 'fist'
    if fingers[1] == 1 and sum(fingers[2:]) == 0:
        return 'point'
    return 'none'

def camera_thread():
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    frame_skip = 1
    skip_counter = 0
    gesture_counter = 0
    while state.running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue
        skip_counter = (skip_counter + 1) % (frame_skip + 1)
        if skip_counter == 0:
            gesture = detect_gesture(frame)
            if gesture != 'none':
                if gesture == state.current_gesture:
                    gesture_counter += 1
                else:
                    gesture_counter = 0
                    state.current_gesture = gesture
                if gesture_counter >= GESTURE_CONFIRM_FRAMES:
                    now = time.time()
                    if now - state.last_gesture_time > GESTURE_COOLDOWN:
                        state.last_gesture_time = now
                        print(f"[GESTURE] {gesture} confirmed!")
                        state.send_queue.put(('gesture', gesture))
                    gesture_counter = 0
            else:
                state.current_gesture = "none"
                gesture_counter = 0
        cv2.putText(frame, f"Gesture: {state.current_gesture}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, f"Conf: {gesture_counter}/{GESTURE_CONFIRM_FRAMES}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands.process(rgb)
        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                mp_drawing.draw_landmarks(
                    frame, hand_landmarks, mp_hands.HAND_CONNECTIONS,
                    mp_drawing.DrawingSpec(color=(0, 0, 255), thickness=2, circle_radius=2),
                    mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2)
                )
        cv2.imshow('Palmsync', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            state.running = False
    cap.release()
    cv2.destroyAllWindows()

# ========== INCOMING POPUP (Receiver Side) ==========
class IncomingPopup:
    def __init__(self, root, file_info, conn, accept_callback, reject_callback):
        self.root = root
        self.file_info = file_info
        self.conn = conn
        self.accept_callback = accept_callback
        self.reject_callback = reject_callback
        self.active = True
        self.timeout_id = None
        
        self.window = tk.Toplevel(root)
        self.window.title("Incoming Transfer")
        self.window.geometry("450x250")
        self.window.protocol("WM_DELETE_WINDOW", self.reject)
        
        # Content
        label = ttk.Label(self.window, text="📥 Incoming File Transfer", font=("Arial", 14, "bold"))
        label.pack(pady=10)
        
        sender_text = f"From: {file_info['sender']}"
        file_text = f"File: {file_info['name']}"
        size_text = f"Size: {file_info['size'] / (1024*1024):.2f} MB"
        
        ttk.Label(self.window, text=sender_text, font=("Arial", 11)).pack(pady=2)
        ttk.Label(self.window, text=file_text, font=("Arial", 11)).pack(pady=2)
        ttk.Label(self.window, text=size_text, font=("Arial", 11)).pack(pady=2)
        
        # Instructions
        ttk.Label(self.window, text="\nGestures:", font=("Arial", 10)).pack(pady=5)
        ttk.Label(self.window, text="  • Fist → Accept", font=("Arial", 10)).pack()
        ttk.Label(self.window, text="  • Palm → Reject", font=("Arial", 10)).pack()
        ttk.Label(self.window, text="  (or press Y/N on keyboard)", font=("Arial", 10)).pack()
        
        # Timer
        self.timer_label = ttk.Label(self.window, text=f"Waiting... (timeout in {INCOMING_TIMEOUT:.0f}s)", font=("Arial", 9))
        self.timer_label.pack(pady=10)
        
        # Start timeout
        self.start_time = time.time()
        self.update_timer()
        
        # Bind keyboard
        self.window.bind('<Key-y>', lambda e: self.accept())
        self.window.bind('<Key-n>', lambda e: self.reject())
        self.window.focus_set()
        
    def update_timer(self):
        if not self.active:
            return
        elapsed = time.time() - self.start_time
        remaining = max(0, INCOMING_TIMEOUT - elapsed)
        self.timer_label.config(text=f"Waiting... (timeout in {remaining:.0f}s)")
        if remaining <= 0:
            self.reject()
        else:
            self.timeout_id = self.window.after(1000, self.update_timer)
    
    def accept(self):
        if not self.active:
            return
        self.active = False
        if self.timeout_id:
            self.window.after_cancel(self.timeout_id)
        self.window.destroy()
        self.accept_callback(self.conn, self.file_info)
    
    def reject(self):
        if not self.active:
            return
        self.active = False
        if self.timeout_id:
            self.window.after_cancel(self.timeout_id)
        self.window.destroy()
        self.reject_callback(self.conn)

# ========== SENDER POPUP GUI (with Progress Bar) ==========
class PopupGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Palmsync – Gesture Transfer")
        self.root.geometry("500x450")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        
        # Content label
        self.content_label = ttk.Label(root, text="Detected: None", font=("Arial", 12))
        self.content_label.pack(pady=10)
        
        # Peer list
        self.peer_listbox = tk.Listbox(root, font=("Consolas", 11), height=6)
        self.peer_listbox.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)
        
        # Status label
        self.status_label = ttk.Label(root, text="Waiting for gesture...", font=("Arial", 10))
        self.status_label.pack(pady=5)
        
        # Progress bar frame
        self.progress_frame = ttk.Frame(root)
        self.progress_frame.pack(fill=tk.X, padx=20, pady=5)
        
        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=2)
        
        # Percentage and speed labels
        self.info_frame = ttk.Frame(self.progress_frame)
        self.info_frame.pack(fill=tk.X)
        self.percent_label = ttk.Label(self.info_frame, text="0%", font=("Arial", 9))
        self.percent_label.pack(side=tk.LEFT)
        self.speed_label = ttk.Label(self.info_frame, text="0 MB/s", font=("Arial", 9))
        self.speed_label.pack(side=tk.RIGHT)
        
        # Buttons
        self.send_button = ttk.Button(root, text="Send Now (Manual)", command=self.manual_send)
        self.send_button.pack(pady=5)
        self.abort_button = ttk.Button(root, text="Abort", command=self.abort)
        self.abort_button.pack(pady=5)
        
        # State
        self.active = False
        self.selected_peer = None
        self.peers = []
        self.content = None
        self.auto_send_timer = None
        self.is_sending = False
        self.root.withdraw()

    def show(self, peers, content):
        self.active = True
        self.peers = peers
        self.content = content
        self.progress_var.set(0)
        self.percent_label.config(text="0%")
        self.speed_label.config(text="0 MB/s")
        self.status_label.config(text="Waiting for gesture...")
        
        if content['type'] == 'file':
            self.content_label.config(text=f"📄 File: {content['title']}")
        elif content['type'] == 'url':
            self.content_label.config(text=f"🌐 URL: {content['title']}")
        else:
            self.content_label.config(text=f"📸 Screenshot: {content['title']}")
        self.peer_listbox.delete(0, tk.END)
        for idx, ip in enumerate(peers):
            self.peer_listbox.insert(tk.END, f"Peer {idx+1}: {ip}")
        if peers:
            self.peer_listbox.selection_set(0)
            self.selected_peer = peers[0]
        else:
            self.status_label.config(text="No peers found. Check network.")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.reset_auto_send()

    def hide(self):
        self.active = False
        self.is_sending = False
        self.root.withdraw()
        if self.auto_send_timer:
            self.root.after_cancel(self.auto_send_timer)
            self.auto_send_timer = None

    def abort(self):
        self.is_sending = False
        self.status_label.config(text="Aborted by user.")
        self.hide()

    def manual_send(self):
        if not self.selected_peer:
            self.status_label.config(text="No peer selected.")
            return
        if not self.content:
            self.status_label.config(text="No content to send.")
            return
        if self.is_sending:
            self.status_label.config(text="Already sending...")
            return
        self.perform_send()

    def perform_send(self):
        peer = self.selected_peer
        content = self.content
        self.is_sending = True
        self.status_label.config(text=f"Sending to {peer}...")
        self.progress_var.set(0)
        self.percent_label.config(text="0%")
        self.speed_label.config(text="0 MB/s")
        threading.Thread(target=self._send_thread, args=(peer, content), daemon=True).start()

    def _send_thread(self, peer, content):
        # Prepare file path
        if content['type'] == 'file':
            file_path = content['path']
        elif content['type'] == 'url':
            temp = tempfile.NamedTemporaryFile(suffix='.txt', delete=False, mode='w')
            temp.write(content['path'])
            temp.close()
            file_path = temp.name
        else:  # screenshot
            file_path = content['path']
        
        # Send with progress callback
        def update_progress(percent, speed):
            self.root.after(0, self._update_progress, percent, speed)
        
        success, message = send_file(peer, file_path, update_progress)
        
        # Cleanup
        if content['type'] == 'url':
            try:
                os.unlink(file_path)
            except:
                pass
        elif content['type'] == 'screenshot':
            try:
                os.unlink(file_path)
            except:
                pass
        
        self.is_sending = False
        self.root.after(0, self._send_result, success, message)

    def _update_progress(self, percent, speed):
        self.progress_var.set(percent)
        self.percent_label.config(text=f"{percent:.1f}%")
        self.speed_label.config(text=f"{speed:.2f} MB/s")
        self.status_label.config(text=f"Transferring... {percent:.1f}%")

    def _send_result(self, success, message):
        if success:
            self.status_label.config(text=f"✅ {message}")
        else:
            self.status_label.config(text=f"❌ {message}")
        self.is_sending = False
        self.root.after(3000, self.hide)

    def gesture_select_next(self):
        if not self.active or not self.peers:
            return
        current = self.peer_listbox.curselection()
        if not current:
            next_idx = 0
        else:
            next_idx = (current[0] + 1) % len(self.peers)
        self.peer_listbox.selection_clear(0, tk.END)
        self.peer_listbox.selection_set(next_idx)
        self.selected_peer = self.peers[next_idx]
        self.status_label.config(text=f"Selected: {self.selected_peer}")
        self.reset_auto_send()

    def reset_auto_send(self):
        if self.auto_send_timer:
            self.root.after_cancel(self.auto_send_timer)
        self.auto_send_timer = self.root.after(int(AUTO_SEND_DELAY * 1000), self.auto_send)

    def auto_send(self):
        if self.active and self.selected_peer and self.content and not self.is_sending:
            self.status_label.config(text="Auto-sending...")
            self.perform_send()
        self.auto_send_timer = None

# ========== RECEIVER CALLBACKS ==========
def receive_accept_callback(conn, file_info):
    try:
        conn.send(b'ACC')
        file_name = file_info['name']
        file_size = file_info['size']
        downloads = Path.home() / 'Downloads'
        downloads.mkdir(exist_ok=True)
        save_path = downloads / f"received_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file_name}"
        with open(save_path, 'wb') as f:
            received = 0
            while received < file_size:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
        conn.close()
        print(f"[RECEIVE] File saved: {save_path}")
        state.receive_queue.put(('file_received', str(save_path)))
    except Exception as e:
        print(f"[RECEIVE] Error accepting file: {e}")
        conn.close()

def receive_reject_callback(conn):
    try:
        conn.send(b'REJ')
        conn.close()
        print("[RECEIVE] Transfer rejected by user.")
    except:
        conn.close()

# ========== MAIN APPLICATION ==========
def main():
    threading.Thread(target=start_discovery_server, daemon=True).start()
    threading.Thread(target=start_file_server, daemon=True).start()
    threading.Thread(target=camera_thread, daemon=True).start()
    
    root = tk.Tk()
    gui = PopupGUI(root)
    
    def process_queues():
        try:
            while True:
                item = state.send_queue.get_nowait()
                if item[0] == 'gesture':
                    gesture = item[1]
                    # If incoming popup is active, gestures control it
                    if state.incoming_popup and state.incoming_popup.active:
                        if gesture == 'fist':
                            state.incoming_popup.accept()
                        elif gesture == 'palm':
                            state.incoming_popup.reject()
                    else:
                        handle_gesture(gesture, gui)
        except queue.Empty:
            pass
        
        try:
            while True:
                item = state.receive_queue.get_nowait()
                if item[0] == 'show_incoming':
                    info = item[1]
                    state.incoming_popup = IncomingPopup(
                        root,
                        info,
                        info['conn'],
                        receive_accept_callback,
                        receive_reject_callback
                    )
                elif item[0] == 'file_received':
                    path = item[1]
                    gui.status_label.config(text=f"📥 File received: {os.path.basename(path)}")
                    if not gui.active:
                        gui.show([], {'type':'file', 'path':path, 'title':os.path.basename(path)})
                        root.after(3000, gui.hide)
        except queue.Empty:
            pass
        
        root.after(100, process_queues)
    
    root.after(100, process_queues)
    print("\n" + "="*50)
    print("Palmsync Gesture Transfer (LAN Only) – Complete Demo")
    print("• Open palm → discover & send")
    print("• Pointing gesture → cycle recipients")
    print("• 5 sec inactivity → auto-send")
    print("• Fist → accept incoming file")
    print("• Palm → reject incoming file")
    print("• Progress bar shows speed and percentage")
    print("• Press 'q' in camera window to quit")
    print("="*50 + "\n")
    root.mainloop()
    state.running = False
    print("Shutdown complete.")

def handle_gesture(gesture, gui):
    if gesture == 'palm':
        if gui.active:
            return
        print("[GESTURE] Open palm – starting discovery...")
        peers = broadcast_discovery()
        content = detect_content()
        state.detected_content = content
        gui.show(peers, content)
        print(f"[DETECT] Content: {content['type']} - {content['title']}")
    elif gesture == 'point':
        if gui.active:
            gui.gesture_select_next()
    elif gesture == 'fist':
        if gui.active:
            gui.abort()

if __name__ == "__main__":
    main()