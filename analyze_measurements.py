#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_measurements.py — μετρικές του σχεδίου αξιολόγησης από τα CSV του MIDIRooms.

  py analyze_measurements.py experiment_RECEIVED_*.csv
  py analyze_measurements.py run.csv --label "sparse_N0_b40_r1" --summary summary.csv --plots

Ανά αρχείο υπολογίζει:
  1. OWD (transit_est_ms): n, μέση τιμή, min, p50/p90/p99/max, jitter = p99 − min, trimmed range (2,5 % κάθε άκρο),
     spikes (> --spike ms) και επεισόδια spike (διάρκεια, πλήθος).
  2. IPDV = transit[i] − transit[i−1] ανά (user, stream), σε σειρά αποστολής: p50/p90/p99 του |IPDV|, peak-to-peak, μέση |IPDV|.
  3. Late % για το buffer της εκτέλεσης (στήλη slack_ms < 0) και εκ των υστέρων για buffer 0,10,20,30,50,80,120 ms
     (late όταν buffer − transit < 0), με effective delay d = max(buffer, transit + 2) (p50/p99).
  4. Κλάσεις παραθύρων 5 s (Perfect / Impaired < 15 % late / Damaged ≥ 15 %) για κάθε buffer.
  5. Effective delay της εκτέλεσης (στήλη eff_delay_ms): p50/p99.
  6. Batch spread: νότες note_on ίδιου (user, stream) με sender_ts εντός --batch-ms ομαδοποιούνται (συγχορδία)·
     spread = max(played_s) − min(played_s) ανά ομάδα με ≥ 2 νότες.
  7. Long-run: ανά λεπτό p99 transit και late %, σύγκριση πρώτου τετάρτου της διάρκειας με το υπόλοιπο.
  8. Clock offset (server_offset_ms): μέσος, drift (κλίση ms/λεπτό), μέγιστη απότομη μεταβολή, μέσο server_jitter_ms.
  9. Απώλεια: κενά στο seq ανά (user, stream) και, αν δοθεί --sent, σύγκριση με το SENT του αποστολέα.
  10. Διαδρομή (via): πλήθος p2p / server.
Περιορισμοί: το transit_est_ms εξαρτάται από το clock offset (ίδια μηχανή: αμελητέο· δύο μηχανήματα: φράσσεται
από RTT/2 − min RTT). Το IPDV δεν επηρεάζεται από σταθερό σφάλμα offset.
"""
import argparse
import csv
import glob
import math
import os
import statistics as st
import sys
from collections import defaultdict

BUFFERS = [0, 10, 20, 30, 50, 80, 120]


def pct(v, q):
    if not v:
        return float("nan")
    v = sorted(v)
    k = (len(v) - 1) * q / 100.0
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def fl(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("status", "ok") not in ("ok", ""):
                continue  # 'nosync': το transit δεν έχει νόημα πριν συγχρονιστεί το ρολόι
            tr, sd, ar = fl(r.get("transit_est_ms")), fl(r.get("sender_ts_s")), fl(r.get("arrival_s"))
            if tr is None or sd is None or ar is None:
                continue
            rows.append({
                "user": r.get("user", ""), "stream": r.get("stream", ""), "type": r.get("type", ""),
                "seq": int(float(r["seq"])) if r.get("seq") not in (None, "") else None,
                "arr": ar, "snd": sd, "transit": tr,
                "slack": fl(r.get("slack_ms")), "late": int(fl(r.get("late")) or 0),
                "played": fl(r.get("played_s")), "lateness": fl(r.get("lateness_ms")),
                "eff": fl(r.get("eff_delay_ms")), "delay": fl(r.get("delay_ms")),
                "adaptive": int(fl(r.get("adaptive")) or 0),
                "off": fl(r.get("server_offset_ms")), "sjit": fl(r.get("server_jitter_ms")),
                "via": r.get("via", ""),
            })
    rows.sort(key=lambda r: r["arr"])
    return rows


def classify(rows, flags, win):
    """flags: λίστα 0/1 (late) ευθυγραμμισμένη με rows. Επιστρέφει % παραθύρων Perfect/Impaired/Damaged."""
    if not rows:
        return (float("nan"),) * 3, 0
    t0 = rows[0]["arr"]
    w = defaultdict(lambda: [0, 0])
    for r, fl_ in zip(rows, flags):
        k = int((r["arr"] - t0) // win)
        w[k][0] += 1
        w[k][1] += fl_
    n = len(w)
    perf = sum(1 for a, l in w.values() if l == 0)
    dam = sum(1 for a, l in w.values() if l / a >= 0.15)
    imp = n - perf - dam
    return (100.0 * perf / n, 100.0 * imp / n, 100.0 * dam / n), n


def analyze(path, a, sent=None):
    rows = load(path)
    M = {"file": os.path.basename(path), "label": a.label}
    if len(rows) < 2:
        print(f"{path}: λίγα δεδομένα ({len(rows)})")
        return None
    tr = [r["transit"] for r in rows]
    n = len(rows)
    dur = rows[-1]["arr"] - rows[0]["arr"]
    M.update(n=n, duration_s=round(dur, 1))
    print(f"\n{'=' * 72}\n{path}\n{'=' * 72}")
    print(f"events {n}   διάρκεια {dur:.1f} s   adaptive={rows[0]['adaptive']}   "
          f"buffer εκτέλεσης {st.median([r['delay'] for r in rows if r['delay'] is not None] or [float('nan')]):.0f} ms")
    via = defaultdict(int)
    for r in rows:
        via[r["via"] or "?"] += 1
    print("via:", ", ".join(f"{k}={v}" for k, v in sorted(via.items())))
    M["via_p2p"], M["via_server"] = via.get("p2p", 0), via.get("server", 0)

    # 1. OWD
    lo, hi = pct(tr, 2.5), pct(tr, 97.5)
    trimmed = [x for x in tr if lo <= x <= hi]
    p99, mn = pct(tr, 99), min(tr)
    print("\n[1] OWD / transit_est_ms (ms)")
    print(f"    μέσος {st.mean(tr):.2f}   min {mn:.2f}   p50 {pct(tr,50):.2f}   p90 {pct(tr,90):.2f}   "
          f"p99 {p99:.2f}   max {max(tr):.2f}")
    print(f"    jitter (p99−min) {p99 - mn:.2f}   trimmed range {max(trimmed) - min(trimmed):.2f}")
    sp = [r for r in rows if r["transit"] > a.spike]
    eps, cur = [], None
    for r in rows:
        if r["transit"] > a.spike:
            if cur is None:
                cur = [r["arr"], r["arr"]]
            else:
                cur[1] = r["arr"]
        elif cur is not None:
            eps.append(cur)
            cur = None
    if cur:
        eps.append(cur)
    print(f"    spikes (> {a.spike:g} ms): {len(sp)} ({100.0*len(sp)/n:.2f} %), επεισόδια {len(eps)}"
          + (f", διάρκεια μέση {st.mean(e[1]-e[0] for e in eps)*1000:.0f} ms, μέγιστη {max(e[1]-e[0] for e in eps)*1000:.0f} ms" if eps else ""))
    M.update(owd_mean=round(st.mean(tr), 3), owd_min=round(mn, 3), owd_p50=round(pct(tr, 50), 3),
             owd_p90=round(pct(tr, 90), 3), owd_p99=round(p99, 3), owd_max=round(max(tr), 3),
             jitter_p99_min=round(p99 - mn, 3), trimmed_range=round(max(trimmed) - min(trimmed), 3),
             spikes=len(sp), spike_episodes=len(eps))

    # 2. IPDV
    by = defaultdict(list)
    for r in rows:
        by[(r["user"], r["stream"])].append(r)
    ip = []
    for k, v in by.items():
        v.sort(key=lambda r: (r["seq"] if r["seq"] is not None else 0, r["snd"]))
        ip += [v[i]["transit"] - v[i - 1]["transit"] for i in range(1, len(v))]
    if ip:
        ab = [abs(x) for x in ip]
        print("\n[2] IPDV (transit[i] − transit[i−1], ms)")
        print(f"    |IPDV| p50 {pct(ab,50):.2f}   p90 {pct(ab,90):.2f}   p99 {pct(ab,99):.2f}   "
              f"peak-to-peak {max(ip) - min(ip):.1f}   μέση |IPDV| {st.mean(ab):.2f}")
        M.update(ipdv_abs_p50=round(pct(ab, 50), 3), ipdv_abs_p90=round(pct(ab, 90), 3),
                 ipdv_abs_p99=round(pct(ab, 99), 3), ipdv_p2p=round(max(ip) - min(ip), 2),
                 ipdv_mean_abs=round(st.mean(ab), 3))

    # 3+4. Late % και effective delay
    late_rec = [1 if (r["slack"] is not None and r["slack"] < 0) else 0 for r in rows]
    eff = [r["eff"] for r in rows if r["eff"] is not None]
    cls, nw = classify(rows, late_rec, a.window)
    print("\n[3] Buffer της εκτέλεσης")
    print(f"    late % (slack<0): {100.0*sum(late_rec)/n:.2f}   (στήλη late=1: {100.0*sum(r['late'] for r in rows)/n:.2f} %)")
    if eff:
        print(f"    effective delay p50 {pct(eff,50):.1f}   p99 {pct(eff,99):.1f} ms")
    print(f"    παράθυρα {a.window:g} s ({nw}): Perfect {cls[0]:.0f} %  Impaired {cls[1]:.0f} %  Damaged {cls[2]:.0f} %")
    M.update(late_pct=round(100.0 * sum(late_rec) / n, 3), eff_p50=round(pct(eff, 50), 2) if eff else "",
             eff_p99=round(pct(eff, 99), 2) if eff else "", win_perfect=round(cls[0], 1),
             win_impaired=round(cls[1], 1), win_damaged=round(cls[2], 1))
    print(f"\n[4] Εκ των υστέρων, για άλλα buffer (late όταν buffer − transit < 0)")
    print(f"    {'buffer':>6} {'late %':>8} {'d p50':>7} {'d p99':>7} {'Perfect':>8} {'Impaired':>9} {'Damaged':>8}")
    for b in BUFFERS:
        fl_ = [1 if b - x < 0 else 0 for x in tr]
        d = [max(b, x + 2.0) for x in tr]
        c, _ = classify(rows, fl_, a.window)
        print(f"    {b:>6} {100.0*sum(fl_)/n:8.2f} {pct(d,50):7.1f} {pct(d,99):7.1f} {c[0]:7.0f}% {c[1]:8.0f}% {c[2]:7.0f}%")
        M[f"late_pct_b{b}"] = round(100.0 * sum(fl_) / n, 3)

    # 6. Batch spread
    on = [r for r in rows if r["type"] == "note_on" and r["played"] is not None]
    groups = []
    for k in by:
        v = sorted((r for r in on if (r["user"], r["stream"]) == k), key=lambda r: r["snd"])
        cur = []
        for r in v:
            if cur and (r["snd"] - cur[0]["snd"]) * 1000 > a.batch_ms:
                groups.append(cur)
                cur = []
            cur.append(r)
        if cur:
            groups.append(cur)
    chords = [g for g in groups if len(g) >= 2]
    if chords:
        sp_ = [(max(r["played"] for r in g) - min(r["played"] for r in g)) * 1000 for g in chords]
        print(f"\n[5] Batch spread ({len(chords)} συγχορδίες, ομαδοποίηση ≤ {a.batch_ms:g} ms στον αποστολέα, ms)")
        print(f"    p50 {pct(sp_,50):.2f}   p95 {pct(sp_,95):.2f}   max {max(sp_):.2f}   μέγεθος ομάδας μέσο {st.mean(len(g) for g in chords):.1f}")
        M.update(chords=len(chords), batch_spread_p50=round(pct(sp_, 50), 3), batch_spread_p95=round(pct(sp_, 95), 3),
                 batch_spread_max=round(max(sp_), 3))
    else:
        print("\n[5] Batch spread: δεν βρέθηκαν συγχορδίες (χρησιμοποίησε το chord_burst.mid)")

    # 7. Long-run
    t0 = rows[0]["arr"]
    per = defaultdict(list)
    for r, l in zip(rows, late_rec):
        per[int((r["arr"] - t0) // 60)].append((r["transit"], l))
    if len(per) >= 4:
        print("\n[6] Long-run (ανά λεπτό)")
        mins = sorted(per)
        q = max(1, len(mins) // 4)
        for m in mins[:3] + (["…"] if len(mins) > 6 else []) + mins[-3:]:
            if m == "…":
                print("    …")
                continue
            v = per[m]
            print(f"    λεπτό {m:3d}: n={len(v):5d}  p99 {pct([x[0] for x in v],99):7.1f} ms  late {100.0*sum(x[1] for x in v)/len(v):5.2f} %")
        first = [x for m in mins[:q] for x in per[m]]
        rest = [x for m in mins[q:] for x in per[m]]
        print(f"    πρώτο τέταρτο: p99 {pct([x[0] for x in first],99):.1f}, late {100.0*sum(x[1] for x in first)/len(first):.2f} %   |   "
              f"υπόλοιπο: p99 {pct([x[0] for x in rest],99):.1f}, late {100.0*sum(x[1] for x in rest)/len(rest):.2f} %")
        M.update(lr_p99_first=round(pct([x[0] for x in first], 99), 2), lr_p99_rest=round(pct([x[0] for x in rest], 99), 2))

    # 8. Clock
    ot = [(r["arr"] - t0, r["off"]) for r in rows if r["off"] is not None]
    if len(ot) > 5:
        # ένα δείγμα ανά αλλαγή τιμής offset (ανά συγχρονισμό)
        pts, last = [], None
        for t, o in ot:
            if o != last:
                pts.append((t, o))
                last = o
        o_ = [p[1] for p in pts]
        sj = [r["sjit"] for r in rows if r["sjit"] is not None]
        print("\n[7] Clock offset (πρός τον server)")
        if len(pts) >= 3:
            xs, ys = [p[0] for p in pts], o_
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            den = sum((x - mx) ** 2 for x in xs)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 60.0 if den else 0.0
            jump = max(abs(o_[i] - o_[i - 1]) for i in range(1, len(o_)))
            print(f"    συγχρονισμοί {len(pts)}   drift {slope:+.3f} ms/λεπτό   μέγιστη μεταβολή μεταξύ διαδοχικών {jump:.2f} ms   "
                  f"μέσο server_jitter {st.mean(sj):.2f} ms")
            M.update(clock_syncs=len(pts), clock_drift_ms_per_min=round(slope, 4), clock_max_jump_ms=round(jump, 3),
                     server_jitter_mean=round(st.mean(sj), 3))
        else:
            print(f"    μόνο {len(pts)} διαφορετικές τιμές offset στη διάρκεια της εκτέλεσης (χρειάζονται ≥ 3 για drift)")

    # 9. Απώλεια — το seq μοιράζεται με άλλα μηνύματα του αποστολέα (π.χ. άλλες ροές), άρα τα κενά του
    # RECEIVED ΔΕΝ είναι απώλεια. Η απώλεια μετριέται μόνο σε σύγκριση με το SENT του αποστολέα.
    if sent:
        with open(sent, newline="", encoding="utf-8") as f:
            srows = list(csv.DictReader(f))
        got = {(r["stream"], r["seq"] and int(float(r["seq"]))) for r in rows}
        miss = [r for r in srows if (r["stream"], int(float(r["seq"]))) not in got]
        print(f"\n[8] Απώλεια: SENT {len(srows)}, RECEIVED {n}, δεν ήρθαν {len(miss)} ({100.0*len(miss)/max(1,len(srows)):.2f} %)")
        M.update(sent=len(srows), lost=len(miss))
    else:
        print("\n[8] Απώλεια: δώσε --sent <SENT.csv> του αποστολέα (το seq μοιράζεται με άλλες ροές, δεν δείχνει απώλεια)")
    if a.plots:
        plots(rows, tr, a, path)
    return M


def plots(rows, tr, a, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib δεν είναι εγκατεστημένο — παραλείπονται τα διαγράμματα")
        return
    base = os.path.splitext(path)[0]
    t0 = rows[0]["arr"]
    fig, ax = plt.subplots(1, 3, figsize=(15, 4))
    ax[0].plot([r["arr"] - t0 for r in rows], tr, ".", ms=2)
    ax[0].set(title="transit_est_ms στον χρόνο", xlabel="s", ylabel="ms")
    ax[1].hist(tr, bins=80)
    ax[1].set(title="Κατανομή OWD", xlabel="ms")
    ax[2].plot(BUFFERS, [100.0 * sum(1 for x in tr if b - x < 0) / len(tr) for b in BUFFERS], "o-")
    ax[2].set(title="Late % ως προς το buffer", xlabel="buffer (ms)", ylabel="late %")
    fig.tight_layout()
    fig.savefig(base + "_plots.png", dpi=130)
    print(f"διαγράμματα: {base}_plots.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("received", nargs="+")
    ap.add_argument("--sent", help="SENT CSV (μόνο για ένα αρχείο RECEIVED)")
    ap.add_argument("--label", default="", help="ετικέτα συνθήκης, π.χ. sparse_N0_b40_r1")
    ap.add_argument("--summary", help="CSV σύνοψης· προστίθεται μία γραμμή ανά αρχείο")
    ap.add_argument("--spike", type=float, default=100.0, help="όριο spike σε ms (100)")
    ap.add_argument("--window", type=float, default=5.0, help="παράθυρο ταξινόμησης σε s (5)")
    ap.add_argument("--batch-ms", type=float, default=3.0, help="ομαδοποίηση συγχορδίας (3 ms)")
    ap.add_argument("--plots", action="store_true")
    a = ap.parse_args()
    files = []
    for p in a.received:
        files += sorted(glob.glob(p)) or [p]
    out = []
    for f in files:
        m = analyze(f, a, a.sent if len(files) == 1 else None)
        if m:
            out.append(m)
    if a.summary and out:
        cols = []
        for m in out:
            for k in m:
                if k not in cols:
                    cols.append(k)
        new = not os.path.exists(a.summary)
        if not new:  # κράτα τις υπάρχουσες στήλες και πρόσθεσε νέες αν χρειαστεί
            with open(a.summary, newline="", encoding="utf-8") as f:
                old = next(csv.reader(f), [])
            cols = old + [c for c in cols if c not in old]
        with open(a.summary, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            if new:
                w.writeheader()
            for m in out:
                w.writerow(m)
        print(f"\nσύνοψη → {a.summary}")


if __name__ == "__main__":
    main()
