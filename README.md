<<<<<<< HEAD
# Driver Drowsiness Detection System

A local webcam application with a dark Tkinter dashboard, real facial landmark detection, continuous eye-closure timing, laptop audio alerts, and a **software-only vehicle simulation**. All application logic is in `main.py`. No Arduino, relay, motor, external assets, audio files, or separately downloaded model files are required.

## Features and technologies

The dashboard includes a mirrored webcam feed with eye landmarks and contours, separate left/right and average EAR readings, processing FPS, color-coded driver status, simulated vehicle/emergency indicators, five session statistics, camera selection, editable thresholds, and Start/Stop/Reset/Mute controls. A scrollable page keeps controls reachable on smaller laptop screens.

Technologies: Python, OpenCV for webcam capture and image processing, MediaPipe Face Mesh for eye landmarks, NumPy for landmark arrays, Pillow for Tkinter image display, Tkinter for the dashboard, Windows winsound for generated audio, and Python threading/queues for responsiveness.

## Project architecture

Everything below is implemented inside `main.py`:

- `Settings` validates camera selection and thresholds.
- `eye_aspect_ratio` calculates the eye-opening measurement.
- `DrowsinessState` owns timing, threshold decisions, simulated motor state and statistics.
- `CameraWorker` owns the webcam and Face Mesh, and sends the newest frame/state through a bounded queue. It releases the webcam in a `finally` block.
- `Alarm` serializes audio in one background thread, with mute and shutdown controls.
- `Dashboard` runs on Tkinter's main thread and handles controls, rendering and session lifecycle.
- `run_self_tests` runs isolated automated checks without accessing the webcam or emitting audio.

The implementation follows the hardware-to-webcam mapping and timing requirements supplied in the project brief. The original uploaded report was not present in the working directory, so its additional contents could not be inspected.

## Windows installation

Install **64-bit CPython 3.11** from [python.org](https://www.python.org/downloads/windows/). Include the Python launcher and **Tcl/Tk and IDLE** in the installer. Python 3.11 is the recommended target; do not use Python 3.13/3.14 with these pins. MediaPipe 0.10.21 provides a [CPython 3.11 Windows x86-64 wheel](https://pypi.org/project/mediapipe/0.10.21/). Newer MediaPipe versions may remove the legacy Face Mesh API, so install the pinned requirements.

Open PowerShell in the project folder:

```powershell
cd C:\Users\Hp\Desktop\driver_drowsiness
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

If PowerShell blocks activation, activation is optional. Use the environment's interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Alternatively, in Command Prompt, activate with `.venv\Scripts\activate.bat` and use the same `python` commands. The first dependency installation needs internet. Once installed, monitoring runs locally and does not upload or save webcam frames.

## Using the dashboard

1. Select camera index **0** for the usual built-in camera. Try 1 or 2 for another camera; indices up to 20 may be typed. Camera indices depend on Windows and attached devices.
2. Leave the default EAR at **0.23**, warning at **2.5 seconds**, and critical at **6.5 seconds** initially.
3. Click **START MONITORING**. Keep one face centered, with both eyes visible and even lighting. The feed is mirrored, preserves its aspect ratio, and displays eye contours, landmarks, EAR and processing FPS.
4. **STOP MONITORING** releases the camera. Start resumes totals with a fresh closure timer. Settings are editable while stopped; all values are validated before starting.
5. **RESET SESSION** clears totals and starts a new closure timer, including while monitoring. **MUTE ALARM** only mutes audio; visual warnings and simulated emergency stop remain active.
6. Close the window to stop audio and release the camera. During camera initialization/driver cleanup, the window stays responsive while waiting for the worker to finish.

Use the left/right and average EAR readings to tune sensitivity while stopped. Higher EAR thresholds classify more frames as closed. Observe your open-eye EAR and closed-eye EAR and choose a threshold between them. There is no universal threshold for all faces, cameras, glasses and poses.

## Detection and simulation behavior

MediaPipe Face Mesh detects one face. Six ordered landmarks per eye are converted to **pixel coordinates** before calculating:

```text
EAR = (distance(p2,p6) + distance(p3,p5)) / (2 * distance(p1,p4))
average EAR = (left EAR + right EAR) / 2
```

The average being strictly below the configured threshold starts the monotonic continuous-closure timer. Open eyes (EAR at or above threshold) immediately reset that timer.

| Observation | Driver status | Alarm | Simulated motor |
| --- | --- | --- | --- |
| Eyes open or closed less than 2.5 s | ALERT | Off | RUNNING |
| Continuously closed at least 2.5 s | DROWSY, red warning | On unless muted | RUNNING |
| Continuously closed at least 6.5 s | CRITICAL DROWSINESS, emergency indicator | On unless muted | STOPPED |
| Eyes reopen | ALERT | Off | RUNNING |
| Face unavailable | FACE NOT DETECTED | Off | UNKNOWN |
| Monitoring stopped | MONITORING STOPPED | Off | IDLE |

Missing face observations reset closure continuity rather than guessing whether the eyes are closed. A processing gap above one second also resets continuity. A stale feed is marked unavailable after two seconds. Tracking loss clears the simulated emergency indicator because current eye state is unknown; this demonstration does not implement a latched vehicle safety controller. All motor and emergency indicators are screen-only. No external device APIs are used.

Detection and camera operations run on a worker thread. Tkinter only updates on its main thread through queues; a bounded queue keeps only the latest frame. One serialized audio thread uses Windows `winsound.Beep`; stop and mute can take up to roughly 180 ms to interrupt a current beep. Volume depends on Windows output settings. An audio failure is shown on the dashboard while visual monitoring continues. Pygame generates an in-memory tone on non-Windows systems.

### Statistics definitions

- **Monitoring time:** accumulated elapsed time while the capture loop runs, including missing-face periods; pauses are excluded.
- **Drowsiness events:** one per continuous closure reaching the warning threshold. Continuing to critical does not count a second event.
- **Alarms triggered:** unmuted warning-stage activations requested by the detector, counted once per event. Beep pulses and later mute/unmute actions do not increment it. This is not proof that an audio device actually emitted sound.
- **Total drowsy time:** observed closure duration beyond the warning threshold (a 6.5 s closure contributes 4.0 s with default settings).
- **Maximum eye closure:** longest observed uninterrupted closure, including normal blinks.

Statistics remain in memory only. Reset or closing the app clears them. Values update when camera observations arrive; the final frame-to-stop fraction is not counted.

## Demonstration and testing

Perform these tests **seated at a desk, never while driving**. Keep the camera pointed at your face while closing your eyes.

1. **Normal blink:** start monitoring and blink naturally, with closures well below 2.5 s. Status remains ALERT, event/alarm counts remain unchanged, and motor remains RUNNING.
2. **Warning:** close both eyes continuously for around 3–4 s. At the first processed observation at or above 2.5 s, expect DROWSY, a red warning and audio (if unmuted). Only one event is counted. Reopen to stop audio and return to ALERT.
3. **Critical:** close both eyes continuously for around 7–8 s. Expect DROWSY at 2.5 s, then CRITICAL DROWSINESS, motor STOPPED and Emergency Stop ACTIVATED at 6.5 s. Event count increases only once for this closure. Reopen to restore RUNNING and clear the alarm/emergency indicator.
4. **Lost tracking:** move out of view. Expect FACE NOT DETECTED, motor UNKNOWN and a cleared closure timer. Return with eyes open to restore ALERT.
5. **Controls and cleanup:** test mute/unmute during a warning, reset while running, stop/start several times, change camera indices while stopped, enter invalid thresholds, and close the window while running. A nonexistent camera should show a recoverable error and enable Start again.

Deterministic logic checks (no dependencies or camera needed):

```powershell
python -m py_compile main.py
python main.py --self-test
python -m pip check
```

`--self-test` exercises EAR geometry, exact 2.5/6.5 s boundaries, blink suppression, one count per event, reopening, face loss, stalled observations, mute counting, reset, invalid configuration and custom thresholds. It is isolated from normal operation and does not simulate webcam detections in the application. `py_compile` may create Python's normal `__pycache__` directory; virtual environments and installed dependencies also create their own files. The authored project consists only of `main.py`, `requirements.txt`, and `README.md`.

### Validation performed during development

The source passed Python syntax compilation and all ten embedded tests using the available bundled Python 3.12.14 interpreter. Tests include observations immediately before and exactly at both timing boundaries, EAR boundary equality, restart without counting paused time, reset during closure, and serial alarm-thread mute/shutdown behavior with a silent test-only audio backend.

A real Tkinter window was launched after supplying local copies of the bundled runtime's Tcl/Tk scripts. GUI smoke checks passed for initialization, settings, mute/unmute, critical-state labels, reset, shutdown, and the scrollable layout at 1020 × 680. A worker initialization error also correctly restored the Start button and disabled Stop. Screenshot capture was unavailable, so no pixel-level visual review is claimed.

Real webcam detection and audible output were not verified: OpenCV and MediaPipe are absent, and pip cannot connect to PyPI because the execution environment denies network sockets (`WinError 10013`). An installation attempt into `.venv` failed; that incomplete test environment was removed to leave only the three project files. Install the recommended official Python version and use the setup commands above in your own Windows terminal with internet access. No successful dependency installation, webcam inference or real audio playback is claimed.

## Troubleshooting and limitations

- **Python not found:** install Python 3.11 with its launcher, reopen PowerShell and check `py -3.11 --version`.
- **No compatible MediaPipe wheel:** use x86-64 CPython 3.11, not a 32-bit/ARM interpreter or Python 3.13+. Create a fresh virtual environment.
- **Missing `mediapipe.solutions`:** reinstall the pinned dependencies in a fresh environment. Do not upgrade MediaPipe independently.
- **Camera unavailable:** Windows Settings > Privacy & security > Camera > enable camera access and desktop app access. Close other camera apps and select another index. DirectShow is attempted first, with Media Foundation as an open-failure fallback.
- **OpenCV import/DLL errors:** install the Microsoft Visual C++ 2015–2022 x64 Redistributable. Do not mix `opencv-python`, `opencv-contrib-python` and headless variants in one environment. The requirements install the contrib variant needed by MediaPipe.
- **Missing Tkinter:** modify the official Python installation to include Tcl/Tk. Tkinter is not a pip package.
- **No audible alarm:** unmute in the dashboard and Windows, check the selected playback device and volume mixer. Audio output is hardware dependent; the counter measures alarm requests.
- **False warnings/missed closures:** improve lighting, face the camera, remove heavy glare and tune EAR. Face Mesh does not certify that detected landmarks represent a truly closed eye; head turns, dark glasses, occlusion and low resolution can cause errors.
- **Camera driver hangs:** Python cannot reliably interrupt every native camera call. Stop/exit waits asynchronously for the worker so camera access is not raced between threads. A broken OS driver may require disconnecting the camera or ending the process.
- This is a college demonstration and portfolio application, **not a validated road safety device**. It cannot guarantee detection of sleepiness or prevent accidents. It monitors a single face, does not identify the driver, and does not replace rest or safe driving practices.

Reference API: [MediaPipe Face Mesh](https://github.com/google-ai-edge/mediapipe/blob/v0.10.21/docs/solutions/face_mesh.md). Defaults reflect the supplied report concept: IR sensor → webcam landmarks, buzzer → laptop audio, relay/motor → software status indicators.
=======
# Driver-Drowsiness-Detection
Real-time Driver Drowsiness Detection System using Python, OpenCV and MediaPipe.
>>>>>>> a3cb162ef809227e9c059d56307de13d368a348d
