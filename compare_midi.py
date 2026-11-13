#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_midi.py — σύγκριση αρχείου που στάλθηκε (REF) με αυτό που κατέγραψε ο Recorder (TEST).
Μέτρα ακεραιότητας και ρυθμού (σχέδιο αξιολόγησης: «Ακεραιότητα» και «IOI error»).

  py compare_midi.py ref.mid test.mid
  py compare_midi.py ref.mid test.mid --csv out.csv --group-ms 3

Ακεραιότητα : πλήθος μηνυμάτων ανά είδος και κανάλι (note_on, note_off, program change, cc),
              Note On/Off που λείπουν ή περισσεύουν, διαφορά σε ύψη/κανάλια.
Αντιστοίχιση: το k-οστό note_on κάθε (κανάλι, ύψος) του REF με το k-οστό του TEST (όχι κατά θέση στο
              αρχείο, ώστε να μην επηρεάζεται από νότες με ίδιο timestamp).
IOI error   : |IOI_TEST − IOI_REF| ανά κανάλι. Onsets του REF που απέχουν ≤ --group-ms (συγχορδία)
              θεωρούνται ένα onset· ο χρόνος του TEST είναι ο μέσος των αντίστοιχων νοτών.
Onset offset: TEST − REF ανά νότα μετά από αφαίρεση της διάμεσης σταθερής καθυστέρησης,
              και drift (ms/λεπτό) από γραμμική προσαρμογή.
Διάρκεια    : διαφορά διάρκειας νοτών (note_off − note_on) για τα αντιστοιχισμένα ζεύγη.
"""
import argparse
import csv
import statistics as st
from collections import Counter, defaultdict

from midi_io import read_events


def pct(v, q):
    if not v:
        return float("nan")
    v = sorted(v)
    k = (len(v) - 1) * q / 100.0
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def counts(ev):
    c = Counter()
    for e in ev:
        c[(e["ch"] + 1, e["kind"])] += 1
    return c


def pair_notes(ev):
    """Επιστρέφει {(ch,note): [(t_on, t_off|None), ...]} με FIFO ταίριασμα on/off."""
    open_, out = defaultdict(list), defaultdict(list)
    for e in sorted(ev, key=lambda e: e["t"]):
        key = (e["ch"], e["a"])
        if e["kind"] == "note_on":
            out[key].append([e["t"], None])
            open_[key].append(len(out[key]) - 1)
        elif e["kind"] == "note_off" and open_[key]:
            out[key][open_[key].pop(0)][1] = e["t"]
    return out


def linfit(x, y):
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    den = sum((a - mx) ** 2 for a in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / den if den else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref")
    ap.add_argument("test")
    ap.add_argument("--group-ms", type=float, default=3.0, help="onsets του REF εντός αυτού = συγχορδία (3)")
    ap.add_argument("--csv", help="αποθήκευση μιας γραμμής σύνοψης (append) για συγκέντρωση εκτελέσεων")
    ap.add_argument("--label", default="", help="ετικέτα συνθήκης για το CSV")
    a = ap.parse_args()

    R, T = read_events(a.ref), read_events(a.test)
    cr, ct = counts(R), counts(T)
    print(f"REF : {a.ref}\nTEST: {a.test}\n")
    print("=== Ακεραιότητα (πλήθος μηνυμάτων) ===")
    keys = sorted(set(cr) | set(ct))
    print(f"{'ch':>3} {'είδος':10s} {'REF':>7} {'TEST':>7} {'διαφορά':>8}")
    for k in keys:
        if k[1] == "other":
            continue
        d = ct[k] - cr[k]
        print(f"{k[0]:>3} {k[1]:10s} {cr[k]:7d} {ct[k]:7d} {d:+8d}" + ("  <--" if d else ""))
    tot = lambda c, kind: sum(v for (ch, kd), v in c.items() if kd == kind)
    miss_on = max(0, tot(cr, "note_on") - tot(ct, "note_on"))
    miss_off = max(0, tot(cr, "note_off") - tot(ct, "note_off"))
    print(f"\nNote On  REF/TEST: {tot(cr,'note_on')}/{tot(ct,'note_on')}   Note Off REF/TEST: {tot(cr,'note_off')}/{tot(ct,'note_off')}")

    pr, pt = pair_notes(R), pair_notes(T)
    matched, lost, extra = [], 0, 0
    for key in set(pr) | set(pt):
        a_, b_ = pr.get(key, []), pt.get(key, [])
        n = min(len(a_), len(b_))
        lost += max(0, len(a_) - len(b_))
        extra += max(0, len(b_) - len(a_))
        for i in range(n):
            matched.append((key[0], key[1], a_[i][0], b_[i][0], a_[i][1], b_[i][1]))
    print(f"Αντιστοιχισμένες νότες: {len(matched)}   χαμένες: {lost}   επιπλέον: {extra}")
    stuck = sum(1 for k in pt.values() for n in k if n[1] is None)
    print(f"Note On χωρίς Note Off στο TEST (κολλημένες νότες): {stuck}")
    if not matched:
        print("Δεν υπάρχουν αντιστοιχισμένες νότες.")
        return
    matched.sort(key=lambda m: m[2])

    # onset offset
    t0r = matched[0][2]
    raw_off = [(m[3] - m[2]) * 1000 for m in matched]
    base = st.median(raw_off)  # σταθερή καθυστέρηση = διάμεσος (πιο ανθεκτικό από την 1η νότα)
    off = [x - base for x in raw_off]
    drift = linfit([m[2] - t0r for m in matched], off) * 60.0  # ms ανά λεπτό
    print("\n=== Onset offset (TEST − REF, μετά την αφαίρεση της διάμεσης σταθερής καθυστέρησης) ===")
    print(f"σταθερή διαφορά (διάμεσος) {base:+.1f} ms")
    print(f"μέσος {st.mean(off):+.2f} ms   SD {st.pstdev(off):.2f}   min {min(off):+.1f}   max {max(off):+.1f}   "
          f"p95 |.| {pct([abs(x) for x in off],95):.1f}   drift {drift:+.2f} ms/min")

    # διάρκειες
    dd = [((m[5] - m[3]) - (m[4] - m[2])) * 1000 for m in matched if m[4] is not None and m[5] is not None]
    if dd:
        print(f"Διάρκεια νοτών (TEST − REF): μέσος {st.mean(dd):+.2f} ms  p95 |.| {pct([abs(x) for x in dd],95):.1f}  max |.| {max(abs(x) for x in dd):.1f}")

    # IOI error ανά κανάλι
    print(f"\n=== IOI error ανά κανάλι (group {a.group_ms:g} ms) ===")
    by_ch = defaultdict(list)
    for m in matched:
        by_ch[m[0]].append(m)
    all_err = []
    print(f"{'ch':>3} {'onsets':>7} {'μέσος':>7} {'p50':>6} {'p95':>6} {'max':>7} {'>3ms':>6} {'>5ms':>6} {'>10ms':>6}")
    for ch in sorted(by_ch):
        ms_ = sorted(by_ch[ch], key=lambda m: m[2])
        groups, cur = [], [ms_[0]]
        for m in ms_[1:]:
            if (m[2] - cur[0][2]) * 1000 <= a.group_ms:
                cur.append(m)
            else:
                groups.append(cur)
                cur = [m]
        groups.append(cur)
        tr = [sum(x[2] for x in g) / len(g) for g in groups]
        tt = [sum(x[3] for x in g) / len(g) for g in groups]
        err = [abs((tt[i] - tt[i - 1]) - (tr[i] - tr[i - 1])) * 1000 for i in range(1, len(tr))]
        all_err += err
        if not err:
            continue
        f = lambda th: 100.0 * sum(1 for e in err if e > th) / len(err)
        print(f"{ch+1:>3} {len(tr):7d} {st.mean(err):7.2f} {pct(err,50):6.2f} {pct(err,95):6.2f} {max(err):7.1f} "
              f"{f(3):5.1f}% {f(5):5.1f}% {f(10):5.1f}%")
    if all_err:
        f = lambda th: 100.0 * sum(1 for e in all_err if e > th) / len(all_err)
        print(f"ΟΛΑ {len(all_err)+1:7d} {st.mean(all_err):7.2f} {pct(all_err,50):6.2f} {pct(all_err,95):6.2f} "
              f"{max(all_err):7.1f} {f(3):5.1f}% {f(5):5.1f}% {f(10):5.1f}%")

    if a.csv and all_err:
        import os
        new = not os.path.exists(a.csv)
        with open(a.csv, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["label", "ref", "test", "ref_on", "test_on", "lost", "extra", "stuck",
                            "ioi_mean_ms", "ioi_p50_ms", "ioi_p95_ms", "ioi_max_ms",
                            "ioi_gt3_pct", "ioi_gt5_pct", "ioi_gt10_pct", "offset_sd_ms", "drift_ms_per_min"])
            f = lambda th: 100.0 * sum(1 for e in all_err if e > th) / len(all_err)
            w.writerow([a.label, a.ref, a.test, tot(cr, "note_on"), tot(ct, "note_on"), lost, extra, stuck,
                        round(st.mean(all_err), 3), round(pct(all_err, 50), 3), round(pct(all_err, 95), 3),
                        round(max(all_err), 2), round(f(3), 2), round(f(5), 2), round(f(10), 2),
                        round(st.pstdev(off), 3), round(drift, 3)])
        print(f"\nΓραμμή προστέθηκε στο {a.csv}")


if __name__ == "__main__":
    main()
