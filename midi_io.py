#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
midi_io.py — ελάχιστος αναγνώστης/εγγραφέας Standard MIDI File (μόνο standard library).
Χρησιμοποιείται από make_test_midis.py και compare_midi.py ώστε να τρέχουν χωρίς επιπλέον πακέτα.

read_events(path) -> list[dict]  ταξινομημένα κατά χρόνο, με κλειδιά:
    t (δευτερόλεπτα, με βάση τις αλλαγές tempo), ch (0-15), kind
    ('note_on','note_off','program','cc','pitch','other'), a, b (data bytes)
write_events(path, events, ppq=480, bpm=120)  events: dict όπως παραπάνω (το t σε δευτερόλεπτα)
"""
import struct


def _vlq(data, i):
    v = 0
    while True:
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def _enc_vlq(v):
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    return bytes(reversed(out))


def read_events(path):
    d = open(path, "rb").read()
    if d[:4] != b"MThd":
        raise ValueError(f"{path}: όχι αρχείο MIDI")
    _fmt, ntr, div = struct.unpack(">HHH", d[8:14])
    if div & 0x8000:
        raise ValueError("SMPTE time division δεν υποστηρίζεται")
    i = 14
    raw, tempos = [], [(0, 500000)]
    for _ in range(ntr):
        if d[i:i + 4] != b"MTrk":
            break
        ln = struct.unpack(">I", d[i + 4:i + 8])[0]
        i += 8
        end = i + ln
        tick, rs = 0, None
        while i < end:
            dt, i = _vlq(d, i)
            tick += dt
            s = d[i]
            if s == 0xFF:
                ty = d[i + 1]
                l, j = _vlq(d, i + 2)
                if ty == 0x51 and l == 3:
                    tempos.append((tick, int.from_bytes(d[j:j + 3], "big")))
                i = j + l
                continue
            if s in (0xF0, 0xF7):
                l, j = _vlq(d, i + 1)
                i = j + l
                continue
            if s & 0x80:
                rs = s
                i += 1
            st = rs
            k = st & 0xF0
            ch = st & 0x0F
            if k in (0xC0, 0xD0):
                a, b = d[i], 0
                i += 1
            else:
                a, b = d[i], d[i + 1]
                i += 2
            if k == 0x90:
                kind = "note_on" if b > 0 else "note_off"
            elif k == 0x80:
                kind = "note_off"
            elif k == 0xC0:
                kind = "program"
            elif k == 0xB0:
                kind = "cc"
            elif k == 0xE0:
                kind = "pitch"
            else:
                kind = "other"
            raw.append((tick, ch, kind, a, b))
        i = end
    tempos.sort()
    # tick -> δευτερόλεπτα
    def sec(t):
        s_, pt, tp = 0.0, 0, 500000
        for tt, v in tempos:
            if tt >= t:
                break
            s_ += (tt - pt) / div * tp / 1e6
            pt, tp = tt, v
        return s_ + (t - pt) / div * tp / 1e6
    cache, ev = {}, []
    for tick, ch, kind, a, b in raw:
        if tick not in cache:
            cache[tick] = sec(tick)
        ev.append({"t": cache[tick], "ch": ch, "kind": kind, "a": a, "b": b})
    ev.sort(key=lambda e: e["t"])
    return ev


def write_events(path, events, ppq=480, bpm=120):
    """Γράφει ένα format-1 αρχείο: track 0 = tempo, track 1 = όλα τα events."""
    tempo = int(60_000_000 / bpm)
    tps = ppq * bpm / 60.0  # ticks ανά δευτερόλεπτο
    def trk(body):
        return b"MTrk" + struct.pack(">I", len(body)) + body
    t0 = _enc_vlq(0) + b"\xFF\x51\x03" + tempo.to_bytes(3, "big") + _enc_vlq(0) + b"\xFF\x2F\x00"
    body, last = b"", 0
    order = {"program": 0, "cc": 0, "note_off": 1, "note_on": 2}
    for e in sorted(events, key=lambda e: (round(e["t"] * tps), order.get(e["kind"], 3))):
        tick = int(round(e["t"] * tps))
        body += _enc_vlq(tick - last)
        last = tick
        ch, a, b = e["ch"] & 0x0F, e["a"] & 0x7F, e.get("b", 0) & 0x7F
        k = e["kind"]
        if k == "note_on":
            body += bytes([0x90 | ch, a, b])
        elif k == "note_off":
            body += bytes([0x80 | ch, a, b])
        elif k == "program":
            body += bytes([0xC0 | ch, a])
        elif k == "cc":
            body += bytes([0xB0 | ch, a, b])
    body += _enc_vlq(0) + b"\xFF\x2F\x00"
    hdr = b"MThd" + struct.pack(">IHHH", 6, 1, 2, ppq)
    with open(path, "wb") as f:
        f.write(hdr + trk(t0) + trk(body))
