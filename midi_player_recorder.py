# -*- coding: utf-8 -*-
"""
midi_tool.py
============
Ανεξάρτητη εφαρμογή MIDI Player + Recorder για το πειραματικό κομμάτι
του MIDIRooms.

TAB 1 — MIDI PLAYER:
    - Δέχεται λίστα από .mid αρχεία (add / remove)
    - Κάθε αρχείο έχει checkbox "ενεργό" — μπορούν να παίξουν πολλά
      ταυτόχρονα ως παράλληλα tracks (ξεκινούν μαζί)
    - Επιλογή MIDI OUTPUT port (dropdown)
    - Play / Stop
    - Ένδειξη προόδου

TAB 2 — MIDI RECORDER:
    - Επιλογή MIDI INPUT port (dropdown)
    - Record / Stop
    - Στιγμή 0 = το πρώτο μήνυμα που λαμβάνεται
    - Εξαγωγή σε .mid (σταθερό 120 BPM, notes σε πραγματικό χρόνο)

Απαιτούμενες βιβλιοθήκες:
    pip install mido python-rtmidi PyQt5
"""

import sys
import time
import os
import mido
from mido import Message, MidiFile, MidiTrack, MetaMessage
from PyQt5 import QtCore, QtWidgets


import re


def display_name(real_name):
    """Όνομα θύρας όπως το βλέπει ο χρήστης: χωρίς τον αύξοντα αριθμό που προσθέτει το WinMM/RtMidi
    (π.χ. 'LoopPort1 2' -> 'LoopPort1'). Το πραγματικό όνομα χρειάζεται μόνο για το άνοιγμα της θύρας."""
    name = (real_name or "").strip()
    if name.upper().startswith("_MR_"):
        # οι θύρες του MIDIRooms έχουν ΔΥΟ αριθμούς στο τέλος ('_MR_0001 4 5'): βγάζουμε όλους
        return re.sub(r"(\s+\d+)+$", "", name)
    return re.sub(r"\s+\d+$", "", name)


def device_choices(real_names):
    """Λίστα (όνομα_προς_εμφάνιση, πραγματικό_όνομα). Σε ίδια ονόματα, από το δεύτερο και μετά προστίθεται ' (2)', ' (3)'."""
    seen = {}
    out = []
    for real in real_names:
        disp = display_name(real)
        seen[disp] = seen.get(disp, 0) + 1
        out.append((disp if seen[disp] == 1 else f"{disp} ({seen[disp]})", real))
    return out


def selected_port(combo):
    """Το πραγματικό όνομα της επιλεγμένης θύρας (όχι αυτό που εμφανίζεται)."""
    return combo.currentData() or combo.currentText()


_zombie_ports = []   # ports που απέτυχαν να κλείσουν: τα κρατάμε ζωντανά (αποφυγή native crash)


def file_bpm(path, default=120.0):
    """BPM του πρώτου set_tempo ενός .mid (αλλιώς 120, που είναι η προεπιλογή του προτύπου)."""
    try:
        for track in MidiFile(path).tracks:
            for m in track:
                if m.type == "set_tempo":
                    return round(mido.tempo2bpm(m.tempo), 2)
    except Exception as e:
        print("⚠️ file_bpm failed:", e)
    return default


def safe_close(port, label=""):
    """Κλείνει ένα mido port. Αν το close() αποτύχει (π.χ. midiInUnprepareHeader στο WinMM),
    το RtMidi αφήνει το αντικείμενο 'connected' και ο destructor ξανακάνει close => crash.
    Άρα σε αποτυχία κρατάμε το αντικείμενο ζωντανό και δεν το αφήνουμε να γίνει destruct."""
    try:
        port.close()
        return True
    except Exception as e:
        print(f"⚠️ close {label} failed: {e} — keeping port alive to avoid native crash")
        _zombie_ports.append(port)
        return False


# ============================================================================
#  MIDI PLAYER — παίζει ένα ή περισσότερα .mid ταυτόχρονα σε ένα output port
# ============================================================================
class MidiPlayerTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.output_port = None          # ανοιχτό mido output
        self.play_timer = None
        self.tracks = []                 # λίστα από dicts: {path, events, index, checkbox, ...}
        self.start_time = None
        self.playing = False

        self._build_ui()
        self.refresh_ports()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # --- Επιλογή output port ---
        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(QtWidgets.QLabel("MIDI Output Port:"))
        self.portCombo = QtWidgets.QComboBox()
        self.portCombo.setMinimumWidth(300)
        port_row.addWidget(self.portCombo)
        self.refreshPortsBtn = QtWidgets.QPushButton("↻ Refresh")
        self.refreshPortsBtn.clicked.connect(self.refresh_ports)
        port_row.addWidget(self.refreshPortsBtn)
        port_row.addStretch(1)
        layout.addLayout(port_row)

        # --- Λίστα αρχείων ---
        layout.addWidget(QtWidgets.QLabel("MIDI αρχεία (τσέκαρε όσα θέλεις να παίξουν μαζί):"))
        self.fileList = QtWidgets.QListWidget()
        layout.addWidget(self.fileList)

        file_btns = QtWidgets.QHBoxLayout()
        self.addFileBtn = QtWidgets.QPushButton("➕ Add MIDI File(s)")
        self.addFileBtn.clicked.connect(self.add_files)
        file_btns.addWidget(self.addFileBtn)
        self.removeFileBtn = QtWidgets.QPushButton("➖ Remove Selected")
        self.removeFileBtn.clicked.connect(self.remove_selected)
        file_btns.addWidget(self.removeFileBtn)
        file_btns.addStretch(1)
        layout.addLayout(file_btns)

        # --- Play / Stop ---
        ctrl_row = QtWidgets.QHBoxLayout()
        self.playBtn = QtWidgets.QPushButton("▶ Play")
        self.playBtn.setMinimumHeight(40)
        self.playBtn.clicked.connect(self.play)
        ctrl_row.addWidget(self.playBtn)
        self.stopBtn = QtWidgets.QPushButton("⏹ Stop")
        self.stopBtn.setMinimumHeight(40)
        self.stopBtn.clicked.connect(self.stop)
        self.stopBtn.setEnabled(False)
        ctrl_row.addWidget(self.stopBtn)
        layout.addLayout(ctrl_row)

        self.statusLabel = QtWidgets.QLabel("Έτοιμο")
        self.statusLabel.setStyleSheet("color: gray;")
        layout.addWidget(self.statusLabel)

    def _lock_port_selection(self, locked: bool):
        """Όσο παίζει το αρχείο, η επιλογή θύρας είναι κλειδωμένη (και το Refresh)."""
        self.portCombo.setEnabled(not locked)
        self.refreshPortsBtn.setEnabled(not locked)

    def refresh_ports(self):
        if self.playing:
            return      # ποτέ αλλαγή λίστας/επιλογής όσο παίζει
        self.portCombo.clear()
        try:
            outs = mido.get_output_names()
        except Exception as e:
            outs = []
            print("⚠️ get_output_names failed:", e)
        for disp, real in device_choices(outs):
            self.portCombo.addItem(disp, real)

    def add_files(self):
        fnames, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select MIDI files", "", "MIDI Files (*.mid *.midi)"
        )
        for f in fnames:
            bpm = file_bpm(f)
            item = QtWidgets.QListWidgetItem(f"{os.path.basename(f)}  ({bpm:g} BPM)")
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked)
            item.setData(QtCore.Qt.UserRole, f)  # πλήρες path
            self.fileList.addItem(item)

    def remove_selected(self):
        for item in self.fileList.selectedItems():
            self.fileList.takeItem(self.fileList.row(item))

    def _load_track_events(self, path):
        """Αποδομεί ένα .mid σε λίστα (abs_time_sec, Message)."""
        events = []
        try:
            mid = MidiFile(path)
        except Exception as e:
            self.statusLabel.setText(f"❌ Σφάλμα φόρτωσης {os.path.basename(path)}: {e}")
            return events
        abs_time = 0.0
        for msg in mid:
            abs_time += msg.time
            if not msg.is_meta:
                events.append((abs_time, msg))
        return events

    def play(self):
        if self.playing:
            return

        port_name = selected_port(self.portCombo)
        if not port_name:
            self.statusLabel.setText("⚠️ Δεν επιλέχθηκε output port")
            return

        # μάζεψε τα ενεργά (τσεκαρισμένα) αρχεία
        active_paths = []
        for i in range(self.fileList.count()):
            item = self.fileList.item(i)
            if item.checkState() == QtCore.Qt.Checked:
                active_paths.append(item.data(QtCore.Qt.UserRole))

        if not active_paths:
            self.statusLabel.setText("⚠️ Δεν υπάρχουν ενεργά αρχεία")
            return

        # άνοιξε το output port
        try:
            self.output_port = mido.open_output(port_name)
        except Exception as e:
            self.statusLabel.setText(f"❌ Δεν άνοιξε το port: {e}")
            return

        # φόρτωσε όλα τα ενεργά tracks
        self.tracks = []
        total_events = 0
        for path in active_paths:
            events = self._load_track_events(path)
            if events:
                self.tracks.append({"path": path, "events": events, "index": 0})
                total_events += len(events)

        if not self.tracks:
            self.statusLabel.setText("⚠️ Κανένα note στα επιλεγμένα αρχεία")
            safe_close(self.output_port, "output")
            self.output_port = None
            return

        self._total_events = total_events
        self._sent_events = 0
        self.start_time = time.perf_counter()
        self.playing = True

        self.play_timer = QtCore.QTimer()
        self.play_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.play_timer.timeout.connect(self._tick)
        self.play_timer.start(1)  # έλεγχος κάθε 1ms

        self._lock_port_selection(True)
        self.playBtn.setEnabled(False)
        self.stopBtn.setEnabled(True)
        self.statusLabel.setText(f"▶️ Παίζει {len(self.tracks)} track(s) — {total_events} events")

    def _tick(self):
        if not self.playing:
            return
        now = time.perf_counter()
        elapsed = now - self.start_time

        all_done = True
        for tr in self.tracks:
            events = tr["events"]
            while tr["index"] < len(events) and events[tr["index"]][0] <= elapsed:
                _, msg = events[tr["index"]]
                tr["index"] += 1
                self._sent_events += 1
                try:
                    self.output_port.send(msg)
                except Exception as e:
                    print("⚠️ send error:", e)
            if tr["index"] < len(events):
                all_done = False

        # ένδειξη προόδου
        if self._total_events:
            pct = 100.0 * self._sent_events / self._total_events
            self.statusLabel.setText(f"▶️ {self._sent_events}/{self._total_events} events ({pct:.0f}%)")

        if all_done:
            self.stop()

    def stop(self):
        self.playing = False
        if self.play_timer:
            try:
                self.play_timer.stop()
            except Exception:
                pass
        # all notes off για ασφάλεια
        if self.output_port:
            try:
                for ch in range(16):
                    self.output_port.send(Message("control_change", channel=ch, control=123, value=0))
            except Exception:
                pass
            safe_close(self.output_port, "output")
            self.output_port = None
        self._lock_port_selection(False)
        self.playBtn.setEnabled(True)
        self.stopBtn.setEnabled(False)
        self.statusLabel.setText("⏹️ Σταματημένο")


# ============================================================================
#  MIDI RECORDER — καταγράφει από input port (BPM και στιγμή 0 επιλέγονται από τον χρήστη)
# ============================================================================
class MidiRecorderTab(QtWidgets.QWidget):
    DEFAULT_BPM = 120.0

    def __init__(self):
        super().__init__()
        self.input_port = None
        self.poll_timer = None
        self.recording = False
        self.recorded = []        # λίστα από (abs_time_sec [perf_counter], Message)
        self.t_press = None       # στιγμή που πατήθηκε το Record

        self._build_ui()
        self.refresh_ports()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(QtWidgets.QLabel("MIDI Input Port:"))
        self.portCombo = QtWidgets.QComboBox()
        self.portCombo.setMinimumWidth(300)
        port_row.addWidget(self.portCombo)
        self.refreshPortsBtn = QtWidgets.QPushButton("↻ Refresh")
        self.refreshPortsBtn.clicked.connect(self.refresh_ports)
        port_row.addWidget(self.refreshPortsBtn)
        port_row.addStretch(1)
        layout.addLayout(port_row)

        opt_row = QtWidgets.QHBoxLayout()
        opt_row.addWidget(QtWidgets.QLabel("BPM του αρχείου:"))
        self.bpmSpin = QtWidgets.QDoubleSpinBox()
        self.bpmSpin.setDecimals(2)
        self.bpmSpin.setRange(20.0, 400.0)
        self.bpmSpin.setValue(self.DEFAULT_BPM)
        opt_row.addWidget(self.bpmSpin)
        opt_row.addSpacing(20)
        opt_row.addWidget(QtWidgets.QLabel("Στιγμή 0:"))
        self.zeroCombo = QtWidgets.QComboBox()
        self.zeroCombo.addItem("Πάτημα Record", "press")
        self.zeroCombo.addItem("Πρώτο μήνυμα", "first")
        opt_row.addWidget(self.zeroCombo)
        opt_row.addStretch(1)
        layout.addLayout(opt_row)

        ctrl_row = QtWidgets.QHBoxLayout()
        self.recordBtn = QtWidgets.QPushButton("⏺ Record")
        self.recordBtn.setMinimumHeight(40)
        self.recordBtn.clicked.connect(self.start_record)
        ctrl_row.addWidget(self.recordBtn)
        self.stopBtn = QtWidgets.QPushButton("⏹ Stop & Save")
        self.stopBtn.setMinimumHeight(40)
        self.stopBtn.clicked.connect(self.stop_record)
        self.stopBtn.setEnabled(False)
        ctrl_row.addWidget(self.stopBtn)
        layout.addLayout(ctrl_row)

        self.statusLabel = QtWidgets.QLabel("Έτοιμο")
        self.statusLabel.setStyleSheet("color: gray;")
        layout.addWidget(self.statusLabel)

        self.countLabel = QtWidgets.QLabel("Events: 0")
        layout.addWidget(self.countLabel)

        layout.addStretch(1)

    def _lock_port_selection(self, locked: bool):
        """Όσο γίνεται καταγραφή, η επιλογή θύρας (και οι ρυθμίσεις αρχείου) είναι κλειδωμένες."""
        self.portCombo.setEnabled(not locked)
        self.refreshPortsBtn.setEnabled(not locked)
        self.bpmSpin.setEnabled(not locked)
        self.zeroCombo.setEnabled(not locked)

    def refresh_ports(self):
        if self.recording:
            return      # ποτέ αλλαγή λίστας/επιλογής όσο γίνεται καταγραφή
        self.portCombo.clear()
        try:
            ins = mido.get_input_names()
        except Exception as e:
            ins = []
            print("⚠️ get_input_names failed:", e)
        for disp, real in device_choices(ins):
            self.portCombo.addItem(disp, real)

    def start_record(self):
        if self.recording:
            return
        port_name = selected_port(self.portCombo)
        if not port_name:
            self.statusLabel.setText("⚠️ Δεν επιλέχθηκε input port")
            return
        try:
            self.input_port = mido.open_input(port_name)
        except Exception as e:
            self.statusLabel.setText(f"❌ Δεν άνοιξε το port: {e}")
            return

        self.recorded = []
        self.t_press = time.perf_counter()
        self.recording = True

        self.poll_timer = QtCore.QTimer()
        self.poll_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start(1)

        self._lock_port_selection(True)
        self.recordBtn.setEnabled(False)
        self.stopBtn.setEnabled(True)
        zero = "πάτημα Record" if self.zeroCombo.currentData() == "press" else "πρώτο μήνυμα"
        self.statusLabel.setText(f"⏺️ Καταγραφή... (στιγμή 0 = {zero}, {self.bpmSpin.value():g} BPM)")

    def _poll(self):
        if not self.recording or not self.input_port:
            return
        for msg in self.input_port.iter_pending():
            if msg.is_meta:
                continue
            self.recorded.append((time.perf_counter(), msg))
            self.countLabel.setText(f"Events: {len(self.recorded)}")

    def stop_record(self):
        self.recording = False
        if self.poll_timer:
            try:
                self.poll_timer.stop()
            except Exception:
                pass
        if self.input_port:
            safe_close(self.input_port, "input")
            self.input_port = None
        self._lock_port_selection(False)

        if not self.recorded:
            self.statusLabel.setText("⚠️ Δεν καταγράφηκε κανένα event")
            self.recordBtn.setEnabled(True)
            self.stopBtn.setEnabled(False)
            return

        # αποθήκευση σε .mid
        fname, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save recording as MIDI",
            f"received_{time.strftime('%Y%m%d_%H%M%S')}.mid",
            "MIDI Files (*.mid)"
        )
        if fname:
            self._save_midi(fname)
            self.statusLabel.setText(f"💾 Αποθηκεύτηκε: {os.path.basename(fname)} ({len(self.recorded)} events)")
        else:
            self.statusLabel.setText("Ακυρώθηκε η αποθήκευση")

        self.recordBtn.setEnabled(True)
        self.stopBtn.setEnabled(False)

    def _save_midi(self, path):
        """Γράφει τα recorded events σε .mid με το BPM του πεδίου. Οι χρόνοι είναι πραγματικοί
        (δευτερόλεπτα), άρα το BPM καθορίζει μόνο σε ποιο μουσικό πλέγμα (μέτρα/beats) πέφτουν στο DAW."""
        mid = MidiFile(type=1)
        ticks_per_beat = mid.ticks_per_beat  # default 480
        tempo = mido.bpm2tempo(self.bpmSpin.value())

        # Track 0: tempo. Έπειτα ΕΝΑ track ανά κανάλι (η δομή κανάλι/όργανο του αρχικού αρχείου)
        tempo_track = MidiTrack()
        tempo_track.append(MetaMessage("track_name", name="MIDIRooms", time=0))
        tempo_track.append(MetaMessage("set_tempo", tempo=tempo, time=0))
        mid.tracks.append(tempo_track)

        t0 = self.t_press if self.zeroCombo.currentData() == "press" else self.recorded[0][0]
        by_channel = {}
        for abs_time, msg in self.recorded:
            ch = getattr(msg, "channel", None)   # τα sysex/clock δεν έχουν κανάλι
            by_channel.setdefault(ch, []).append((abs_time, msg))

        for ch in sorted(by_channel, key=lambda c: (c is None, c)):
            track = MidiTrack()
            track.append(MetaMessage("track_name", name=("Other" if ch is None else f"Channel {ch + 1}"), time=0))
            last_ticks = 0
            for abs_time, msg in by_channel[ch]:
                rel_time = max(0.0, abs_time - t0)
                # στρογγυλοποίηση των ΑΠΟΛΥΤΩΝ ticks (όχι του κάθε delta)
                abs_ticks = max(last_ticks, int(round(mido.second2tick(rel_time, ticks_per_beat, tempo))))
                track.append(msg.copy(time=abs_ticks - last_ticks))
                last_ticks = abs_ticks
            mid.tracks.append(track)

        mid.save(path)


# ============================================================================
#  Κύριο παράθυρο με τα δύο tabs
# ============================================================================
class MidiToolWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MIDI Tool — Player & Recorder")
        self.resize(600, 500)

        tabs = QtWidgets.QTabWidget()
        self.player_tab = MidiPlayerTab()
        self.recorder_tab = MidiRecorderTab()
        tabs.addTab(self.player_tab, "🎵 MIDI Player")
        tabs.addTab(self.recorder_tab, "⏺ MIDI Recorder")
        self.setCentralWidget(tabs)

    def closeEvent(self, event):
        """Κλείσιμο παραθύρου: σταμάτα καθαρά player/recorder πριν βγει η εφαρμογή."""
        try:
            if self.player_tab.playing:
                self.player_tab.stop()
            rec = self.recorder_tab
            if rec.recording:
                rec.recording = False
                if rec.poll_timer:
                    rec.poll_timer.stop()
                if rec.input_port:
                    safe_close(rec.input_port, "input")
                    rec.input_port = None
        except Exception as e:
            print("⚠️ closeEvent cleanup:", e)
        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = MidiToolWindow()
    win.show()
    sys.exit(app.exec_())