#!/usr/bin/env python3
"""
Palmsync – Gesture Controlled Secure File Transfer (WAN)
Complete client with progress bar, gestures, and receiver confirmation.
"""

import os
import sys
import time
import threading
import queue
import tempfile
from datetime import datetime
from pathlib import Path

# GUI
import tkinter as tk
from tkinter import ttk

# Computer vision
import cv2
import mediapipe as mp

# HTTP for WAN
import requests

# Windows specific
import win32gui
import win32process
import win32con

# URL grabbing
from pynput.keyboard import Key, Controller as KeyboardController

# ========== CONFIGURATION ==========
RELAY_SERVER = "https://palmsync-relay-xxxxxx-uc.a.run.app"  # REPLACE with your URL
DEVICE_ID = None
POLL_INTERVAL = 2.0
GESTURE_COOLDOWN = 0.8
AUTO_SEND_DELAY = 5.0
GESTURE_CONFIRM_FRAMES = 5
INCOMING_TIMEOUT = 10.0
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# ========== GLOBAL STATE ==========
class State:
    def __init__(self):
        self.running = True
        self.current_gesture = "none"
        self.device_id = DEVICE_ID
        self.remote_peers = []
        self.selected_peer = None
        self.detected_content = None
        self.send_queue = queue.Queue()
        self.receive_queue = queue.Queue()
        self.popup_active = False
        self.gesture_counter = 0
        self.last_gesture_time = 0
        self.incoming_popup = None
        self.is_sending = False

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

# ========== WAN RELAY CLIENT ==========
def register_device():
    try:
        response = requests.post(f"{RELAY_SERVER}/api/register", json={'device_id': state.device_id}, timeout=5)
        return response.status_code == 200
    except:
        print("[WARN] Could not register with relay server.")
        return False

def keepalive():
    try:
        requests.post(f"{RELAY_SERVER}/api/keepalive", json={'device_id': state.device_id}, timeout=3)
    except:
        pass

def fetch_peers():
    try:
        response = requests.get(f"{RELAY_SERVER}/api/peers", timeout=5)
        if response.status_code == 200:
            peers = response.json()
            return [p for p in peers if p['device_id'] != state.device_id]
        return []
    except:
        return []

def initiate_transfer(receiver_id, file_path, filename, file_size):
    try:
        response = requests.post(f"{RELAY_SERVER}/api/transfer/initiate", json={
            'filename': filename,
            'size': file_size,
            'sender': state.device_id,
            'receiver': receiver_id
        }, timeout=5)
        if response.status_code == 200:
            return response.json().get('transfer_id')
        return None
    except:
        return None

def upload_chunk(transfer_id, index, data):
    try:
        files = {'chunk': ('chunk', data)}
        form_data = {'transfer_id': transfer_id, 'index': index}
        response = requests.post(f"{RELAY_SERVER}/api/transfer/upload_chunk", data=form_data, files=files, timeout=10)
        return response.status_code == 200
    except:
        return False

def download_chunks(transfer_id, total_chunks, save_path):
    try:
        with open(save_path, 'wb') as f:
            for i in range(total_chunks):
                response = requests.get(
                    f"{RELAY_SERVER}/api/transfer/download_chunk",
                    params={'transfer_id': transfer_id, 'index': i},
                    timeout=30
                )
                if response.status_code != 200:
                    return False
                f.write(response.content)
        return True
    except:
        return False

def check_incoming_transfers():
    try:
        response = requests.get(
            f"{RELAY_SERVER}/api/transfer/incoming",
            params={'device_id': state.device_id},
            timeout=5
        )
        if response.status_code == 200:
            incoming = response.json()
            for t in incoming:
                state.receive_queue.put(('show_incoming', t))
            return incoming
        return []
    except:
        return []

def accept_transfer_remote(transfer_id):
    try:
        requests.post(f"{RELAY_SERVER}/api/transfer/accept", json={'transfer_id': transfer_id}, timeout=5)
        return True
    except:
        return False

def reject_transfer_remote(transfer_id):
    try:
        requests.post(f"{RELAY_SERVER}/api/transfer/reject", json={'transfer_id': transfer_id}, timeout=5)
        return True
    except:
        return False

def complete_transfer_remote(transfer_id):
    try:
        requests.post(f"{RELAY_SERVER}/api/transfer/complete", json={'transfer_id': transfer_id}, timeout=5)
        return True
    except:
        return False

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
        cv2.imshow('Palmsync', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            state.running = False
    cap.release()
    cv2.destroyAllWindows()

# ========== POLLING THREAD ==========
def polling_thread():
    while state.running:
        if state.device_id:
            try:
                check_incoming_transfers()
            except:
                pass
        time.sleep(POLL_INTERVAL)

# ========== INCOMING POPUP ==========
class IncomingPopup:
    def __init__(self, root, transfer_info, accept_callback, reject_callback):
        self.root = root
        self.transfer_info = transfer_info
        self.accept_callback = accept_callback
        self.reject_callback = reject_callback
        self.active = True
        self.timeout_id = None
        self.window = tk.Toplevel(root)
        self.window.title("Incoming Transfer")
        self.window.geometry("450x250")
        self.window.protocol("WM_DELETE_WINDOW", self.reject)
        label = ttk.Label(self.window, text="📥 Incoming File Transfer", font=("Arial", 14, "bold"))
        label.pack(pady=10)
        ttk.Label(self.window, text=f"From: {transfer_info['sender']}", font=("Arial", 11)).pack(pady=2)
        ttk.Label(self.window, text=f"File: {transfer_info['filename']}", font=("Arial", 11)).pack(pady=2)
        ttk.Label(self.window, text=f"Size: {transfer_info['size'] / (1024*1024):.2f} MB", font=("Arial", 11)).pack(pady=2)
        ttk.Label(self.window, text="\nGestures:", font=("Arial", 10)).pack(pady=5)
        ttk.Label(self.window, text="  • Fist → Accept", font=("Arial", 10)).pack()
        ttk.Label(self.window, text="  • Palm → Reject", font=("Arial", 10)).pack()
        ttk.Label(self.window, text="  (or press Y/N on keyboard)", font=("Arial", 10)).pack()
        self.timer_label = ttk.Label(self.window, text=f"Waiting... (timeout in {INCOMING_TIMEOUT:.0f}s)", font=("Arial", 9))
        self.timer_label.pack(pady=10)
        self.start_time = time.time()
        self.update_timer()
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
        self.accept_callback(self.transfer_info)
    def reject(self):
        if not self.active:
            return
        self.active = False
        if self.timeout_id:
            self.window.after_cancel(self.timeout_id)
        self.window.destroy()
        self.reject_callback(self.transfer_info)

# ========== SENDER POPUP GUI ==========
class PopupGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Palmsync – Gesture Transfer")
        self.root.geometry("500x450")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.content_label = ttk.Label(root, text="Detected: None", font=("Arial", 12))
        self.content_label.pack(pady=10)
        self.peer_listbox = tk.Listbox(root, font=("Consolas", 11), height=6)
        self.peer_listbox.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)
        self.status_label = ttk.Label(root, text="Waiting for gesture...", font=("Arial", 10))
        self.status_label.pack(pady=5)
        self.progress_frame = ttk.Frame(root)
        self.progress_frame.pack(fill=tk.X, padx=20, pady=5)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=2)
        self.info_frame = ttk.Frame(self.progress_frame)
        self.info_frame.pack(fill=tk.X)
        self.percent_label = ttk.Label(self.info_frame, text="0%", font=("Arial", 9))
        self.percent_label.pack(side=tk.LEFT)
        self.speed_label = ttk.Label(self.info_frame, text="0 MB/s", font=("Arial", 9))
        self.speed_label.pack(side=tk.RIGHT)
        self.send_button = ttk.Button(root, text="Send Now (Manual)", command=self.manual_send)
        self.send_button.pack(pady=5)
        self.abort_button = ttk.Button(root, text="Abort", command=self.abort)
        self.abort_button.pack(pady=5)
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
        for idx, peer in enumerate(peers):
            self.peer_listbox.insert(tk.END, f"{peer['device_id']} ({peer['ip']})")
        if peers:
            self.peer_listbox.selection_set(0)
            self.selected_peer = peers[0]
        else:
            self.status_label.config(text="No peers found. Check server.")
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
        self.status_label.config(text=f"Sending to {peer['device_id']}...")
        self.progress_var.set(0)
        self.percent_label.config(text="0%")
        self.speed_label.config(text="0 MB/s")
        threading.Thread(target=self._send_thread, args=(peer, content), daemon=True).start()

    def _send_thread(self, peer, content):
        if content['type'] == 'file':
            file_path = content['path']
        elif content['type'] == 'url':
            temp = tempfile.NamedTemporaryFile(suffix='.txt', delete=False, mode='w')
            temp.write(content['path'])
            temp.close()
            file_path = temp.name
        else:
            file_path = content['path']
        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)
        transfer_id = initiate_transfer(peer['device_id'], file_path, filename, file_size)
        if not transfer_id:
            self.root.after(0, self._send_result, False, "Failed to initiate transfer")
            return
        chunk_size = CHUNK_SIZE
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        sent = 0
        start_time = time.time()
        with open(file_path, 'rb') as f:
            for i in range(total_chunks):
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                success = upload_chunk(transfer_id, i, chunk)
                if not success:
                    self.root.after(0, self._send_result, False, "Upload failed")
                    return
                sent += len(chunk)
                progress = (sent / file_size) * 100
                elapsed = time.time() - start_time
                speed = sent / elapsed / (1024 * 1024) if elapsed > 0 else 0
                self.root.after(0, self._update_progress, progress, speed)
        complete_transfer_remote(transfer_id)
        self.root.after(0, self._send_result, True, "Sent successfully!")

    def _update_progress(self, percent, speed):
        self.progress_var.set(percent)
        self.percent_label.config(text=f"{percent:.1f}%")
        self.speed_label.config(text=f"{speed:.2f} MB/s")
        self.status_label.config(text=f"Uploading... {percent:.1f}%")

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
        self.status_label.config(text=f"Selected: {self.selected_peer['device_id']}")
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

# ========== MAIN APPLICATION ==========
def main():
    global DEVICE_ID
    if len(sys.argv) > 1:
        DEVICE_ID = sys.argv[1]
    else:
        DEVICE_ID = input("Enter your device ID (e.g., 'laptop_a'): ").strip()
    state.device_id = DEVICE_ID
    if not register_device():
        print("[ERROR] Could not connect to relay server.")
        return
    threading.Thread(target=camera_thread, daemon=True).start()
    threading.Thread(target=polling_thread, daemon=True).start()
    def keepalive_thread():
        while state.running:
            keepalive()
            time.sleep(10)
    threading.Thread(target=keepalive_thread, daemon=True).start()
    root = tk.Tk()
    gui = PopupGUI(root)
    def process_queues():
        try:
            while True:
                item = state.send_queue.get_nowait()
                if item[0] == 'gesture':
                    gesture = item[1]
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
    print("Palmsync Gesture Transfer (WAN)")
    print(f"Device ID: {state.device_id}")
    print(f"Relay Server: {RELAY_SERVER}")
    print("Open palm → discover & send")
    print("Pointing gesture → cycle recipients")
    print("5 sec inactivity → auto-send")
    print("Fist → accept incoming file")
    print("Palm → reject incoming file")
    print("Press 'q' in camera window to quit")
    print("="*50 + "\n")
    root.mainloop()
    state.running = False
    print("Shutdown complete.")

def handle_gesture(gesture, gui):
    if gesture == 'palm':
        if gui.active:
            return
        print("[GESTURE] Open palm – fetching peers...")
        peers = fetch_peers()
        if not peers:
            print("[DISCOVERY] No peers found on relay server.")
            gui.status_label.config(text="No peers found. Check server.")
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

def receive_accept_callback(transfer_info):
    transfer_id = transfer_info['transfer_id']
    filename = transfer_info['filename']
    file_size = transfer_info['size']
    try:
        response = requests.get(f"{RELAY_SERVER}/api/transfer/status", params={'transfer_id': transfer_id}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            total_chunks = data['total_chunks']
        else:
            return
    except:
        return
    accept_transfer_remote(transfer_id)
    downloads = Path.home() / 'Downloads'
    downloads.mkdir(exist_ok=True)
    save_path = downloads / f"received_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
    success = download_chunks(transfer_id, total_chunks, save_path)
    if success:
        print(f"[RECEIVE] File saved: {save_path}")
        state.receive_queue.put(('file_received', str(save_path)))
    else:
        print("[RECEIVE] Download failed.")

def receive_reject_callback(transfer_info):
    transfer_id = transfer_info['transfer_id']
    reject_transfer_remote(transfer_id)
    print("[RECEIVE] Transfer rejected by user.")

if __name__ == "__main__":
    main()