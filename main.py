"""Driver Drowsiness Detection System — all application logic in this file.

Python 3.11 (64-bit) recommended. Run `python main.py --self-test` for
dependency-free logic checks; normal operation always uses a real webcam.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import math
import os
import queue
import sys
import threading
import time
import traceback


@dataclass(frozen=True)
class Settings:
    camera: int = 0
    ear: float = 0.23
    warning: float = 2.5
    critical: float = 6.5

    def validate(self):
        if not 0 <= self.camera <= 20:
            raise ValueError("Camera index must be between 0 and 20.")
        if not math.isfinite(self.ear) or not 0.10 <= self.ear <= 0.40:
            raise ValueError("EAR threshold must be between 0.10 and 0.40.")
        if not all(math.isfinite(v) for v in (self.warning, self.critical)):
            raise ValueError("Timing thresholds must be finite numbers.")
        if not 0.2 <= self.warning < self.critical <= 60:
            raise ValueError("Use 0.2 <= warning < critical <= 60 seconds.")
        return self


def eye_aspect_ratio(points):
    """Six ordered, pixel-space points: corners 0/3, lid pairs 1/5 and 2/4."""
    def distance(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])
    width = distance(points[0], points[3])
    if width < 1e-6:
        return None
    return (distance(points[1], points[5]) +
            distance(points[2], points[4])) / (2.0 * width)


class DrowsinessState:
    """Worker-owned state. All timing uses a monotonic clock.

    Monitoring time includes no-face time but excludes pauses. Drowsy time
    counts only the portion of verified closures beyond the warning threshold.
    A missing face or a >1 second observation gap breaks continuity.
    """
    def __init__(self, settings):
        self.settings = settings
        self.reset()

    def reset(self):
        self.monitoring = 0.0
        self.drowsy_time = 0.0
        self.maximum = 0.0
        self.events = 0
        self.alarms = 0
        self.closed_since = None
        self.last_time = None
        self.previous_duration = 0.0
        self.event_counted = False

    def interrupt(self):
        self.closed_since = None
        self.previous_duration = 0.0
        self.event_counted = False

    def restore_totals(self, snapshot):
        """Resume a stopped session without carrying closure state or pause time."""
        self.reset()
        for key in ("monitoring", "events", "alarms", "drowsy_time", "maximum"):
            setattr(self, key, snapshot[key])

    def update(self, ear, now, muted=False):
        gap = 0.0 if self.last_time is None else max(0.0, now - self.last_time)
        self.monitoring += gap
        self.last_time = now
        if gap > 1.0:
            self.interrupt()
        valid = ear is not None and math.isfinite(ear)
        duration = 0.0
        status = "FACE NOT DETECTED"
        if valid:
            status = "ALERT"
            if ear < self.settings.ear:
                if self.closed_since is None:
                    self.closed_since = now
                duration = max(0.0, now - self.closed_since)
                self.maximum = max(self.maximum, duration)
                self.drowsy_time += max(0.0, duration - self.settings.warning) - max(
                    0.0, self.previous_duration - self.settings.warning)
                self.previous_duration = duration
                if duration >= self.settings.warning:
                    status = "DROWSY"
                    if not self.event_counted:
                        self.events += 1
                        # Alarm activations requested while unmuted, not audio pulses.
                        self.alarms += int(not muted)
                        self.event_counted = True
                if duration >= self.settings.critical:
                    status = "CRITICAL DROWSINESS"
            else:
                self.interrupt()
        else:
            self.interrupt()
        warning = status in ("DROWSY", "CRITICAL DROWSINESS")
        critical = status == "CRITICAL DROWSINESS"
        return dict(status=status, ear=ear if valid else None, closure=duration,
                    warning=warning, critical=critical,
                    motor="STOPPED" if critical else "RUNNING" if valid else "UNKNOWN",
                    monitoring=self.monitoring, events=self.events, alarms=self.alarms,
                    drowsy_time=self.drowsy_time, maximum=self.maximum)


class Alarm:
    """One serialized audio thread, no external sound files and no overlap."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = False
        self.muted = False
        self.shutdown = threading.Event()
        self.error = None
        self.sound = None
        self.channel = None
        self.pygame = None
        self.winsound = None
        self.thread = threading.Thread(target=self._loop, daemon=True, name="AudioAlarm")
        self.thread.start()

    def set(self, active=None, muted=None):
        with self.lock:
            if active is not None:
                self.active = active
            if muted is not None:
                self.muted = muted

    def _wanted(self):
        with self.lock:
            return self.active and not self.muted

    def _prepare(self):
        if sys.platform == "win32":
            import winsound
            self.winsound = winsound
        else:
            import pygame
            import numpy as np
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
            t = np.arange(int(22050 * 0.22)) / 22050
            # Short fade avoids clicks. Sound stays entirely in memory.
            wave = np.sin(2 * np.pi * 1050 * t)
            fade = np.minimum(1.0, np.minimum(t / 0.01, (0.22 - t) / 0.01))
            samples = (wave * fade * 26000).astype(np.int16)
            self.sound = pygame.sndarray.make_sound(samples)
            self.pygame = pygame

    def _loop(self):
        try:
            self._prepare()
            while not self.shutdown.is_set():
                if self._wanted():
                    if self.winsound:
                        # Beep blocks only this thread. Stop/mute latency <=180 ms.
                        self.winsound.Beep(1150, 180)
                    else:
                        self.channel = self.sound.play()
                        for _ in range(11):
                            if self.shutdown.wait(0.02) or not self._wanted():
                                if self.channel:
                                    self.channel.stop()
                                break
                    self.shutdown.wait(0.08)
                else:
                    self.shutdown.wait(0.025)
        except Exception as exc:
            self.error = f"Audio unavailable: {exc}"
        finally:
            if self.pygame:
                self.pygame.mixer.quit()

    def close(self):
        self.set(active=False)
        self.shutdown.set()


# Imported only for webcam/GUI operation; the logic self-test needs no packages.
cv2 = np = mp = Image = ImageTk = tk = ttk = messagebox = None
LEFT_EYE = (362, 385, 387, 263, 373, 380)
RIGHT_EYE = (33, 160, 158, 133, 153, 144)


class CameraWorker(threading.Thread):
    def __init__(self, settings, frames, notices, alarm, muted):
        super().__init__(daemon=True, name="WebcamDetection")
        self.settings = settings
        self.frames = frames
        self.notices = notices
        self.alarm = alarm
        self.muted = muted
        self.stop_event = threading.Event()
        self.reset_event = threading.Event()
        self.state = DrowsinessState(settings)

    def publish(self, value):
        # A single newest frame prevents latency and unbounded memory growth.
        try:
            self.frames.put_nowait(value)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(value)
            except queue.Full:
                pass

    def run(self):
        capture = mesh = None
        try:
            if not hasattr(mp, "solutions"):
                raise RuntimeError("Face Mesh API is missing. Install the exact versions "
                                   "from requirements.txt in a Python 3.11 environment.")
            mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False, max_num_faces=1, refine_landmarks=True,
                min_detection_confidence=0.6, min_tracking_confidence=0.6)
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if os.name == "nt" else [cv2.CAP_ANY]
            for backend in backends:
                if self.stop_event.is_set():
                    return
                capture = cv2.VideoCapture(self.settings.camera, backend)
                if capture.isOpened():
                    break
                capture.release()
                capture = None
            if capture is None or not capture.isOpened():
                raise RuntimeError(f"Cannot open webcam {self.settings.camera}. Close Teams, "
                                   "Zoom or other camera apps, enable Windows camera permissions, "
                                   "or select another camera index.")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
            capture.set(cv2.CAP_PROP_FPS, 30)
            self.notices.put(("ready", "Camera connected. Monitoring is live."))
            previous = time.monotonic()
            self.state.last_time = previous
            fps = 0.0
            failures = 0
            while not self.stop_event.is_set():
                ok, frame = capture.read()
                if self.stop_event.is_set():
                    break
                if not ok or frame is None:
                    failures += 1
                    self.alarm.set(active=False)
                    snapshot = self.state.update(None, time.monotonic(), self.muted.is_set())
                    self.publish((None, snapshot, 0.0, None, None, time.monotonic()))
                    if failures >= 10:
                        raise RuntimeError("Webcam stopped delivering frames. Check the USB "
                                           "connection and camera permissions, then restart monitoring.")
                    self.stop_event.wait(0.05)
                    continue
                failures = 0
                frame = cv2.flip(frame, 1)
                if frame.shape[1] > 960:
                    frame = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                result = mesh.process(rgb)
                rgb.flags.writeable = True
                if self.stop_event.is_set():
                    break
                left = right = average = None
                eyes = []
                if result.multi_face_landmarks:
                    landmarks = result.multi_face_landmarks[0].landmark
                    height, width = frame.shape[:2]
                    for indices in (LEFT_EYE, RIGHT_EYE):
                        eyes.append([(landmarks[i].x * width, landmarks[i].y * height)
                                     for i in indices])
                    left, right = (eye_aspect_ratio(points) for points in eyes)
                    if left is not None and right is not None:
                        average = (left + right) / 2
                now = time.monotonic()
                if self.reset_event.is_set():
                    self.state.reset()
                    self.state.last_time = now
                    self.reset_event.clear()
                snapshot = self.state.update(average, now, self.muted.is_set())
                self.alarm.set(active=snapshot["warning"])
                delta = now - previous
                if delta > 0:
                    fps = 0.85 * fps + 0.15 / delta if fps else 1 / delta
                previous = now
                color = (255, 100, 110) if snapshot["warning"] else (67, 224, 173)
                for points in eyes:
                    contour = np.array(points, dtype=np.int32)
                    cv2.polylines(rgb, [contour], True, color, 1, cv2.LINE_AA)
                    for x, y in contour:
                        cv2.circle(rgb, (int(x), int(y)), 2, color, -1, cv2.LINE_AA)
                cv2.putText(rgb, f"EAR {average:.3f}" if average is not None else "NO FACE",
                            (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
                cv2.putText(rgb, f"{fps:.1f} FPS", (20, 62),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (225, 231, 240), 1, cv2.LINE_AA)
                if snapshot["warning"]:
                    cv2.rectangle(rgb, (2, 2), (rgb.shape[1]-3, rgb.shape[0]-3), color, 5)
                    cv2.putText(rgb, snapshot["status"], (20, rgb.shape[0] - 22),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
                self.publish((rgb, snapshot, fps, left, right, now))
        except Exception as exc:
            self.notices.put(("error", str(exc)))
            traceback.print_exc()
        finally:
            self.alarm.set(active=False)
            if capture is not None:
                capture.release()
            if mesh is not None:
                mesh.close()
            self.notices.put(("finished", "Camera released. Monitoring stopped."))


class Dashboard:
    BG = "#0b1120"
    PANEL = "#151f32"
    INNER = "#10192a"
    TEXT = "#edf3fc"
    MUTED = "#a6b5cd"
    TEAL = "#5ce0c1"
    RED = "#ff7387"
    AMBER = "#ffc66b"

    def __init__(self, root):
        self.root = root
        self.root.title("Driver Drowsiness Detection System | Webcam Dashboard")
        self.root.geometry("1280x880")
        self.root.minsize(1020, 680)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = None
        self.frames = queue.Queue(maxsize=1)
        self.notices = queue.Queue()
        self.muted = threading.Event()
        self.alarm = Alarm()
        self.closing = False
        self.stopping = False
        self.last_packet = None
        self.last_received = None
        self.history = deque(maxlen=160)
        self.snapshot = DrowsinessState(Settings()).update(None, 0)
        self.photo = None
        self.controls = []
        self.last_error = None
        self._style()
        self._build()
        self.root.after(35, self.tick)

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=self.INNER, background=self.PANEL,
                        foreground=self.TEXT, arrowcolor=self.TEAL, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", self.INNER)],
                  foreground=[("readonly", self.TEXT)])
        style.configure("Closure.Horizontal.TProgressbar", background=self.TEAL,
                        troughcolor=self.INNER, borderwidth=0, thickness=8)

    def label(self, parent, text, size=11, color=None, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"),
                        fg=color or self.TEXT,
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs)

    def card(self, parent, title):
        frame = tk.Frame(parent, bg=self.PANEL, padx=18, pady=14,
                         highlightbackground="#25334b", highlightthickness=1)
        self.label(frame, title, 10, self.MUTED, True).pack(anchor="w", pady=(0, 10))
        return frame

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command,
                         bg=self.TEAL if primary else "#25334b",
                         fg=self.BG if primary else self.TEXT,
                         activebackground="#9bf0dc" if primary else "#364965",
                         activeforeground=self.BG if primary else self.TEXT,
                         disabledforeground="#78879d", relief="flat", bd=0,
                         font=("Segoe UI", 10, "bold"), padx=16, pady=12,
                         cursor="hand2", highlightthickness=1,
                         highlightbackground="#3b4c68", highlightcolor=self.TEAL)

    def _build(self):
        # A scrollable page keeps every control reachable on 768-pixel laptops.
        viewport = tk.Canvas(self.root, bg=self.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=viewport.yview)
        scrollbar.pack(side="right", fill="y")
        viewport.pack(side="left", fill="both", expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        self.page = tk.Frame(viewport, bg=self.BG)
        page_window = viewport.create_window((0, 0), window=self.page, anchor="nw")

        def resize_page(event=None):
            viewport.itemconfigure(page_window, width=viewport.winfo_width(),
                                   height=max(viewport.winfo_height(), self.page.winfo_reqheight()))
            viewport.configure(scrollregion=viewport.bbox("all"))

        viewport.bind("<Configure>", resize_page)
        self.page.bind("<Configure>", resize_page)
        self.root.bind("<MouseWheel>", lambda event: viewport.yview_scroll(
            -int(event.delta / 120), "units"))
        header = tk.Frame(self.page, bg=self.BG)
        header.pack(fill="x", padx=24, pady=(18, 14))
        self.label(header, "DRIVER DROWSINESS DETECTION SYSTEM", 21, bold=True).pack(anchor="w")
        row = tk.Frame(header, bg=self.BG)
        row.pack(fill="x", pady=(3, 0))
        self.label(row, "Real-Time Webcam Monitoring", 11, self.MUTED).pack(side="left")
        self.live_badge = self.label(row, "OFFLINE  /  READY", 10, self.TEAL, True)
        self.live_badge.pack(side="right")

        body = tk.Frame(self.page, bg=self.BG)
        body.pack(fill="both", expand=True, padx=24)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2, minsize=350)
        body.rowconfigure(0, weight=1)
        camera = self.card(body, "01  /  LIVE CAMERA")
        camera.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.preview = tk.Canvas(camera, bg="#070d18", highlightthickness=0, height=260)
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<Configure>", lambda event: self.render_video())
        self.ear_line = self.label(camera, "LEFT EAR  —     RIGHT EAR  —     AVERAGE  —     FPS  —",
                                    10, self.TEAL, True)
        self.ear_line.pack(anchor="w", pady=(10, 0))

        side = tk.Frame(body, bg=self.BG)
        side.grid(row=0, column=1, sticky="nsew")
        status = self.card(side, "02  /  DRIVER STATUS")
        status.pack(fill="x")
        self.status_label = self.label(status, "READY TO MONITOR", 19, self.MUTED, True)
        self.status_label.pack(anchor="w")
        self.status_hint = self.label(status, "Start the camera to begin your session.", 10,
                                      self.MUTED, wraplength=335, justify="left")
        self.status_hint.pack(anchor="w", pady=(5, 12))
        self.closure_label = self.label(status, "Continuous eye closure   0.00 s", 11)
        self.closure_label.pack(anchor="w")
        self.progress = ttk.Progressbar(status, style="Closure.Horizontal.TProgressbar",
                                       maximum=6.5, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 5))
        self.threshold_caption = self.label(status, "WARNING  2.5 s                 CRITICAL  6.5 s",
                                             9, self.MUTED)
        self.threshold_caption.pack(anchor="w")

        simulation = self.card(side, "03  /  VEHICLE SIMULATION")
        simulation.pack(fill="x", pady=(12, 0))
        self.motor_label = self.label(simulation, "Motor Status:  IDLE", 15, self.MUTED, True)
        self.motor_label.pack(anchor="w")
        self.warning_label = self.label(simulation, "Warning Status:  INACTIVE", 11)
        self.warning_label.pack(anchor="w", pady=(7, 0))
        self.emergency_label = self.label(simulation, "Emergency Stop:  NOT ACTIVATED", 11)
        self.emergency_label.pack(anchor="w", pady=(7, 0))
        self.label(simulation, "SOFTWARE SIMULATION ONLY • No hardware control",
                   9, self.MUTED).pack(anchor="w", pady=(12, 0))

        stats = tk.Frame(self.page, bg=self.BG)
        stats.pack(fill="x", padx=24, pady=12)
        self.stat_labels = {}
        for col, (key, title, value) in enumerate([
            ("monitoring", "MONITORING TIME", "00:00:00"),
            ("events", "DROWSINESS EVENTS", "0"),
            ("alarms", "ALARMS TRIGGERED", "0"),
            ("drowsy_time", "TOTAL DROWSY TIME", "0.0 s"),
            ("maximum", "MAX EYE CLOSURE", "0.0 s"),
        ]):
            stats.columnconfigure(col, weight=1, uniform="stats")
            box = self.card(stats, title)
            box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            label = self.label(box, value, 20, self.TEXT, True)
            label.pack(anchor="w")
            self.stat_labels[key] = label

        settings = self.card(self.page, "04  /  SESSION CONTROLS")
        settings.pack(fill="x", padx=24)
        fields = tk.Frame(settings, bg=self.PANEL)
        fields.pack(fill="x")
        self.variables = {"camera": tk.StringVar(value="0"), "ear": tk.StringVar(value="0.23"),
                          "warning": tk.StringVar(value="2.5"), "critical": tk.StringVar(value="6.5")}
        for col, (key, title) in enumerate([
            ("camera", "Webcam index (0 = built-in)"), ("ear", "EAR sensitivity (higher = more sensitive)"),
            ("warning", "Warning threshold (seconds)"), ("critical", "Critical threshold (seconds)")
        ]):
            fields.columnconfigure(col, weight=1)
            item = tk.Frame(fields, bg=self.PANEL)
            item.grid(row=0, column=col, sticky="ew", padx=(0, 12 if col < 3 else 0))
            self.label(item, title, 9, self.MUTED, wraplength=180, justify="left").pack(anchor="w")
            if key == "camera":
                widget = ttk.Combobox(item, textvariable=self.variables[key],
                                      values=tuple(str(i) for i in range(6)), width=12)
            else:
                widget = tk.Entry(item, textvariable=self.variables[key], bg=self.INNER,
                                  fg=self.TEXT, insertbackground=self.TEAL, relief="flat",
                                  font=("Segoe UI", 11), highlightthickness=1,
                                  highlightbackground="#364965", highlightcolor=self.TEAL,
                                  disabledbackground=self.INNER, disabledforeground="#8292ab")
            widget.pack(fill="x", ipady=6, pady=(5, 0))
            self.controls.append(widget)
        actions = tk.Frame(settings, bg=self.PANEL)
        actions.pack(fill="x", pady=(14, 0))
        self.start_button = self.button(actions, "START MONITORING", self.start, True)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = self.button(actions, "STOP MONITORING", self.stop)
        self.stop_button.pack(side="left", padx=(0, 8))
        self.stop_button.config(state="disabled")
        self.reset_button = self.button(actions, "RESET SESSION", self.reset)
        self.reset_button.pack(side="left", padx=(0, 8))
        self.mute_button = self.button(actions, "MUTE ALARM", self.toggle_mute)
        self.mute_button.pack(side="right")
        self.footer = self.label(self.page, "Ready. Settings apply when monitoring starts. Keep your face well lit.",
                                 10, self.MUTED, anchor="w", wraplength=1100, justify="left")
        self.footer.pack(fill="x", padx=24, pady=(10, 12))
        self.render_video()

    def read_settings(self):
        try:
            return Settings(int(self.variables["camera"].get()), float(self.variables["ear"].get()),
                            float(self.variables["warning"].get()),
                            float(self.variables["critical"].get())).validate()
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Check your settings. {exc}") from exc

    def start(self):
        if self.worker is not None or self.closing:
            return
        try:
            settings = self.read_settings()
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return
        self.last_error = None
        self.stopping = False
        self.last_packet = None
        self.last_received = time.monotonic()
        self.history.clear()
        self.alarm.set(active=False)
        for widget in self.controls:
            widget.config(state="disabled")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.progress.config(maximum=settings.critical)
        self.threshold_caption.config(text=f"WARNING  {settings.warning:g} s                 CRITICAL  {settings.critical:g} s")
        self.status_label.config(text="CONNECTING CAMERA", fg=self.AMBER)
        self.status_hint.config(text="Loading Face Mesh and opening the selected webcam…")
        self.live_badge.config(text="CONNECTING", fg=self.AMBER)
        self.footer.config(text="Opening webcam. This can take a few seconds.")
        self.worker = CameraWorker(settings, self.frames, self.notices, self.alarm, self.muted)
        # Preserve totals across stop/start, but never a previous closure timer.
        self.worker.state.restore_totals(self.snapshot)
        self.worker.start()

    def stop(self):
        if self.worker is None:
            return
        self.stopping = True
        self.worker.stop_event.set()
        self.alarm.set(active=False)
        self.stop_button.config(state="disabled")
        self.reset_button.config(state="disabled")
        self.footer.config(text="Stopping monitoring and releasing camera…")

    def reset(self):
        if self.worker and not self.stopping:
            self.worker.reset_event.set()
        else:
            self.snapshot = DrowsinessState(Settings()).update(None, 0)
            self.update_stats()
        self.history.clear()
        self.alarm.set(active=False)
        self.footer.config(text="Session reset. A continuing eye closure starts a new timer.")

    def toggle_mute(self):
        if self.muted.is_set():
            self.muted.clear()
        else:
            self.muted.set()
        self.alarm.set(muted=self.muted.is_set())
        self.mute_button.config(text="UNMUTE ALARM" if self.muted.is_set() else "MUTE ALARM")
        self.footer.config(text="Audio muted; visual warnings remain active." if self.muted.is_set()
                           else "Audio enabled. Check Windows volume and output device.")

    def update_stats(self):
        for key, label in self.stat_labels.items():
            value = self.snapshot[key]
            if key == "monitoring":
                seconds = int(value)
                text = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
            elif key in ("events", "alarms"):
                text = str(value)
            else:
                text = f"{value:.1f} s"
            label.config(text=text)

    def show_snapshot(self, snapshot, fps, left, right):
        self.snapshot = snapshot
        status = snapshot["status"]
        color = self.RED if snapshot["warning"] else self.TEAL if status == "ALERT" else self.AMBER
        self.status_label.config(text=status, fg=color)
        hint = {"ALERT": "Eyes open. Stay attentive and take regular breaks.",
                "DROWSY": "WARNING: Prolonged eye closure. Audio warning active.",
                "CRITICAL DROWSINESS": "EMERGENCY: Simulated motor power cut. Eyes closed too long.",
                "FACE NOT DETECTED": "Tracking unavailable. Face the camera; closure timer reset."}[status]
        if status == "ALERT" and snapshot["closure"] > 0:
            hint = "Eyes closed. Timing continuous closure before warning."
        if snapshot["warning"] and self.muted.is_set():
            hint = hint.replace("Audio warning active.", "Audio is muted.")
        self.status_hint.config(text=hint, fg=color if snapshot["warning"] else self.MUTED)
        self.closure_label.config(text=f"Continuous eye closure   {snapshot['closure']:.2f} s")
        self.progress.config(value=snapshot["closure"])
        self.motor_label.config(text=f"Motor Status:  {snapshot['motor']}",
                                fg=self.RED if snapshot["critical"] else self.TEAL if snapshot["motor"] == "RUNNING" else self.AMBER)
        self.warning_label.config(text="Warning Status:  " + ("ACTIVE" if snapshot["warning"] else "INACTIVE"), fg=color)
        emergency = "ACTIVATED" if snapshot["critical"] else "NOT ACTIVATED"
        self.emergency_label.config(text=f"Emergency Stop:  {emergency}", fg=self.RED if snapshot["critical"] else self.TEXT)
        def fmt(value):
            return "—" if value is None else f"{value:.3f}"
        self.ear_line.config(text=f"LEFT {fmt(left)}    RIGHT {fmt(right)}    AVG {fmt(snapshot['ear'])}    {fps:.1f} FPS")
        self.live_badge.config(text="LIVE  /  " + ("AUDIO MUTED" if self.muted.is_set() else "MONITORING"), fg=color)
        self.update_stats()

    def render_video(self):
        width, height = self.preview.winfo_width(), self.preview.winfo_height()
        if width < 10 or height < 10:
            return
        self.preview.delete("all")
        if self.last_packet is None or self.last_packet[0] is None:
            self.preview.create_text(width/2, height/2 - 18, text="WEBCAM MONITOR",
                                     fill=self.TEXT, font=("Segoe UI", 20, "bold"))
            self.preview.create_text(width/2, height/2 + 18,
                                     text="Select a camera and start monitoring" if not self.worker else "Waiting for a camera frame…",
                                     fill=self.MUTED, font=("Segoe UI", 11))
            return
        image = Image.fromarray(self.last_packet[0])
        image.thumbnail((width, height), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(image=image)
        self.preview.create_image(width/2, height/2, image=self.photo, anchor="center")

    def tick(self):
        try:
            while True:
                kind, text = self.notices.get_nowait()
                if kind == "error":
                    self.last_error = text
                    self.footer.config(text="Camera error: " + text, fg=self.RED)
                elif kind == "ready" and not self.stopping:
                    self.footer.config(text=text, fg=self.MUTED)
        except queue.Empty:
            pass
        packet = None
        try:
            while True:
                packet = self.frames.get_nowait()
        except queue.Empty:
            pass
        if packet is not None:
            self.last_packet = packet
            self.last_received = packet[5]
            if not self.stopping and not self.closing:
                self.show_snapshot(packet[1], packet[2], packet[3], packet[4])
                self.render_video()
            else:
                self.snapshot = packet[1]
                self.update_stats()
        # Stop displaying a stale observation as if it were a current reading.
        if (self.worker and self.last_received and not self.stopping and
                time.monotonic() - self.last_received > 2.0):
            self.alarm.set(active=False)
            self.status_label.config(text="CAMERA STALLED", fg=self.AMBER)
            self.status_hint.config(text="Waiting for fresh frames. Monitoring is unavailable.")
            self.motor_label.config(text="Motor Status:  UNKNOWN", fg=self.AMBER)
            self.warning_label.config(text="Warning Status:  INACTIVE", fg=self.MUTED)
            self.emergency_label.config(text="Emergency Stop:  NOT ACTIVATED", fg=self.MUTED)
            self.live_badge.config(text="NO FRESH FRAMES", fg=self.AMBER)
        if self.alarm.error:
            self.footer.config(text=self.alarm.error + " | Visual detection remains active.", fg=self.AMBER)
        if self.worker is not None and not self.worker.is_alive():
            self.worker = None
            self.stopping = False
            self.alarm.set(active=False)
            self.last_packet = None
            self.render_video()
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.reset_button.config(state="normal")
            for widget in self.controls:
                widget.config(state="normal")
            self.status_label.config(text="CAMERA ERROR" if self.last_error else "MONITORING STOPPED",
                                     fg=self.RED if self.last_error else self.MUTED)
            self.status_hint.config(text=self.last_error or "Session paused. Start again to resume.")
            self.motor_label.config(text="Motor Status:  IDLE", fg=self.MUTED)
            self.warning_label.config(text="Warning Status:  INACTIVE", fg=self.MUTED)
            self.emergency_label.config(text="Emergency Stop:  NOT ACTIVATED", fg=self.MUTED)
            self.closure_label.config(text="Continuous eye closure   0.00 s")
            self.progress.config(value=0)
            self.ear_line.config(text="LEFT EAR  —     RIGHT EAR  —     AVERAGE  —     FPS  —")
            self.live_badge.config(text="OFFLINE  /  READY", fg=self.MUTED)
            if not self.last_error:
                self.footer.config(text="Monitoring stopped. Camera released; session totals retained.", fg=self.MUTED)
        if self.closing and self.worker is None:
            self.alarm.close()
            self.root.destroy()
            return
        self.root.after(35, self.tick)

    def close(self):
        self.closing = True
        self.start_button.config(state="disabled")
        self.stop()
        self.alarm.close()
        self.footer.config(text="Closing. Waiting for the webcam worker to release the camera…")


def run_self_tests():
    """Deterministic state tests; never replace or fake the actual camera pipeline."""
    import unittest

    class LogicTests(unittest.TestCase):
        def advance(self, state, start, end, ear=0.15, muted=False):
            result = None
            # Samples <=1 second apart preserve verified observation continuity.
            steps = max(1, math.ceil((end - start) / 0.25))
            for index in range(steps + 1):
                result = state.update(ear, start + (end-start) * index / steps, muted)
            return result

        def test_ear_geometry(self):
            self.assertAlmostEqual(eye_aspect_ratio([(0, 0), (1, 1), (3, 1),
                                                    (4, 0), (3, -1), (1, -1)]), 0.5)
            self.assertIsNone(eye_aspect_ratio([(0, 0)] * 6))

        def test_blink_and_thresholds(self):
            state = DrowsinessState(Settings())
            self.assertEqual(self.advance(state, 0, 0.2)["events"], 0)
            state.update(0.3, 0.25)
            self.assertEqual(self.advance(state, 1, 3.49)["status"], "ALERT")
            warning = state.update(0.15, 3.5)
            self.assertEqual(warning["status"], "DROWSY")
            self.assertEqual(warning["events"], 1)
            result = self.advance(state, 3.5, 7.5)
            self.assertEqual(result["status"], "CRITICAL DROWSINESS")
            self.assertEqual(result["motor"], "STOPPED")
            self.assertEqual(result["events"], 1)
            self.assertEqual(result["alarms"], 1)
            self.assertAlmostEqual(result["drowsy_time"], 4.0)
            result = state.update(0.3, 7.6)
            self.assertEqual(result["status"], "ALERT")
            self.assertEqual(result["motor"], "RUNNING")
            self.assertEqual(result["closure"], 0)
            self.assertFalse(result["warning"])

        def test_missing_face_and_gap(self):
            state = DrowsinessState(Settings())
            self.advance(state, 0, 3)
            missing = state.update(None, 3.1)
            self.assertEqual(missing["status"], "FACE NOT DETECTED")
            self.assertEqual(missing["motor"], "UNKNOWN")
            self.assertFalse(missing["warning"])
            self.assertEqual(state.update(0.1, 3.2)["closure"], 0)
            self.assertEqual(state.update(0.1, 10)["closure"], 0)

        def test_mute_reset_and_multiple_events(self):
            state = DrowsinessState(Settings())
            result = self.advance(state, 0, 3, muted=True)
            self.assertEqual((result["events"], result["alarms"]), (1, 0))
            state.update(0.3, 3.1)
            result = self.advance(state, 3.2, 6.2)
            self.assertEqual((result["events"], result["alarms"]), (2, 1))
            state.reset()
            result = state.update(0.3, 50)
            self.assertEqual(result["monitoring"], 0)
            self.assertEqual(result["maximum"], 0)
            self.assertEqual(result["events"], 0)

        def test_configuration(self):
            Settings().validate()
            for settings in (Settings(ear=float("nan")), Settings(ear=0.01),
                             Settings(warning=7), Settings(critical=float("inf")),
                             Settings(camera=-1), Settings(warning=-1)):
                with self.assertRaises(ValueError):
                    settings.validate()

        def test_custom_thresholds_and_invalid_ear(self):
            state = DrowsinessState(Settings(ear=0.2, warning=1, critical=2))
            self.assertEqual(self.advance(state, 0, 1)["status"], "DROWSY")
            self.assertEqual(self.advance(state, 1, 2)["status"], "CRITICAL DROWSINESS")
            self.assertEqual(state.update(float("nan"), 2.1)["status"], "FACE NOT DETECTED")

        def test_immediately_before_and_at_boundaries(self):
            state = DrowsinessState(Settings())
            self.advance(state, 0, 2.25)
            self.assertEqual(state.update(0.15, 2.499)["status"], "ALERT")
            self.assertEqual(state.update(0.15, 2.5)["status"], "DROWSY")
            self.advance(state, 2.5, 6.25)
            self.assertEqual(state.update(0.15, 6.499)["motor"], "RUNNING")
            self.assertEqual(state.update(0.15, 6.5)["motor"], "STOPPED")
            # Equality at the EAR boundary means open, not closed.
            self.assertEqual(state.update(0.23, 6.6)["status"], "ALERT")

        def test_restart_retains_totals_without_pause_or_closure(self):
            old = DrowsinessState(Settings())
            snapshot = self.advance(old, 0, 7)
            resumed = DrowsinessState(Settings())
            resumed.restore_totals(snapshot)
            first = resumed.update(0.15, 100)
            self.assertEqual(first["monitoring"], snapshot["monitoring"])
            self.assertEqual(first["closure"], 0)
            self.assertEqual(first["status"], "ALERT")
            self.assertEqual(first["events"], 1)
            second = self.advance(resumed, 100, 102.5)
            self.assertEqual(second["events"], 2)
            self.assertAlmostEqual(second["monitoring"], snapshot["monitoring"] + 2.5)

        def test_reset_during_closure_starts_new_timer(self):
            state = DrowsinessState(Settings())
            self.advance(state, 0, 7)
            state.reset()
            first = state.update(0.15, 7.1)
            self.assertEqual((first["closure"], first["events"], first["alarms"]), (0, 0, 0))
            self.assertFalse(first["critical"])

        def test_serial_alarm_mute_and_shutdown(self):
            # Test-only audio backend: no real sound and no change to webcam code.
            started = threading.Event()
            release = threading.Event()
            calls = []

            class AudioProbe:
                def Beep(self, frequency, duration):
                    calls.append(threading.get_ident())
                    started.set()
                    release.wait(0.5)

            class TestAlarm(Alarm):
                def _prepare(self):
                    self.winsound = AudioProbe()

            alarm = TestAlarm()
            try:
                alarm.set(active=True, muted=True)
                self.assertFalse(started.wait(0.06))
                alarm.set(muted=False)
                self.assertTrue(started.wait(1))
                # Repeated requests do not create threads or overlapping sounds.
                for _ in range(20):
                    alarm.set(active=True)
                alarm.set(active=False)
                alarm.close()
                release.set()
                alarm.thread.join(1)
                self.assertFalse(alarm.thread.is_alive())
                self.assertEqual(len(calls), 1)
                self.assertIsNone(alarm.error)
            finally:
                release.set()
                alarm.close()
                alarm.thread.join(1)

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(LogicTests))
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run logic tests without webcam or dependencies")
    arguments = parser.parse_args()
    if arguments.self_test:
        return run_self_tests()
    global cv2, np, mp, Image, ImageTk, tk, ttk, messagebox
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
        import cv2
        import numpy as np
        import mediapipe as mp
        from PIL import Image, ImageTk
    except ImportError as exc:
        text = (f"A required dependency could not be loaded: {exc}\n\n"
                "Use 64-bit Python 3.11 with Tcl/Tk, activate your virtual environment, "
                "then run: python -m pip install -r requirements.txt")
        print(text, file=sys.stderr)
        if tk is not None:
            try:
                error_root = tk.Tk()
                error_root.withdraw()
                messagebox.showerror("Setup required", text)
                error_root.destroy()
            except Exception:
                pass
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Cannot initialize Tkinter: {exc}\nInstall Python 3.11 with the "
              "Tcl/Tk component and run in a Windows desktop session.", file=sys.stderr)
        return 1
    Dashboard(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
