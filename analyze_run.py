#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_run.py
==============
Ανάλυση των experiment_RECEIVED_*.csv που γράφει η εφαρμογή (checkbox "Record CSV").

Χρήση:
    python analyze_run.py experiment_RECEIVED_*.csv
    python analyze_run.py experiment_RECEIVED_a.csv experiment_RECEIVED_b.csv --sent experiment_SENT_x.csv
    python analyze_run.py experiment_RECEIVED_*.csv --out results

Για κάθε αρχείο (= ένα run) τυπώνει:
  - νότες (note_on), απώλεια (κενά στο seq / σύγκριση με SENT αν δοθεί)
  - transit_est_ms : min, p50, p99, p99.9, max  και  jitter = p99 - min
  - IPDV (std της διαφοράς διαδοχικών transit)
  - late% = ποσοστό νοτών που ήρθαν αργά (late=1, slack_ms < 0)
  - lateness_ms (p50, p99, max)
  - delay_ms (buffer που εφαρμόστηκε): min / mean / max και % χρόνου πάνω από τη βάση
  - eff_delay_ms : mean, p95, p99
Γράφει: <out>/summary_runs.csv και <out>/<run>_timeline.png
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd


def load(path):
    df = pd.read_csv(path)
    for c in ("transit_est_ms", "slack_ms", "lateness_ms", "delay_ms", "buf_base_ms",
              "buf_extra_ms", "eff_delay_ms", "arrival_s", "send_ms", "seq", "note", "vel", "late"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def pct(x, q):
    x = x.dropna()
    return float(np.percentile(x, q)) if len(x) else float("nan")


def seq_gaps(df):
    """Πόσα seq λείπουν ανά (user, stream) — ελάχιστη εκτίμηση απώλειας από τη μεριά του παραλήπτη."""
    missing = 0
    for _, g in df.groupby(["user", "stream"]):
        s = np.sort(g["seq"].dropna().astype(int).unique())
        if len(s) > 1:
            missing += int((s[-1] - s[0] + 1) - len(s))
    return missing


def analyze(path, sent_path=None, outdir=None):
    df = load(path)
    name = os.path.basename(path).replace("experiment_RECEIVED_", "").replace(".csv", "")
    ok = df[df["status"] == "ok"] if "status" in df.columns else df
    n_all = len(df)
    ons = ok[(ok["type"] == "note_on") & (ok["vel"] > 0)]
    t = ok["transit_est_ms"]

    res = {"run": name, "rows": n_all, "rows_ok": len(ok), "note_on": len(ons),
           "dropped_or_nosync": n_all - len(ok)}
    res["seq_missing"] = seq_gaps(df)
    if sent_path:
        s = pd.read_csv(sent_path)
        res["sent_rows"] = len(s)
        res["lost_vs_sent"] = max(0, len(s) - n_all)

    res.update({
        "transit_min": t.min(), "transit_p50": pct(t, 50), "transit_p99": pct(t, 99),
        "transit_p999": pct(t, 99.9), "transit_max": t.max(),
        "jitter_p99_min": pct(t, 99) - t.min(),
    })
    ipdv = np.diff(ok.sort_values("arrival_s")["transit_est_ms"].to_numpy())
    res["ipdv_std"] = float(np.std(ipdv)) if len(ipdv) > 1 else float("nan")
    res["late_pct"] = 100.0 * float((ok["late"] > 0).mean()) if len(ok) else float("nan")
    lt = ok["lateness_ms"]
    res.update({"lateness_p50": pct(lt, 50), "lateness_p99": pct(lt, 99), "lateness_max": lt.max()})
    d = ok["delay_ms"]
    base = ok["buf_base_ms"]
    res.update({"delay_min": d.min(), "delay_mean": d.mean(), "delay_max": d.max(),
                "pct_above_base": 100.0 * float((ok["buf_extra_ms"] > 0.5).mean()) if len(ok) else float("nan"),
                "base_ms": float(base.median()) if len(ok) else float("nan"),
                "adaptive": int(ok["adaptive"].max()) if "adaptive" in ok.columns and len(ok) else ""})
    e = ok["eff_delay_ms"]
    res.update({"eff_mean": e.mean(), "eff_p95": pct(e, 95), "eff_p99": pct(e, 99)})

    if outdir:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            o = ok.sort_values("arrival_s")
            x = o["arrival_s"] - o["arrival_s"].iloc[0]
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(x, o["transit_est_ms"], ".", ms=3, label="transit_est_ms")
            ax.plot(x, o["delay_ms"], "-", lw=1.2, label="delay_ms (buffer)")
            ax.set_xlabel("s από την πρώτη νότα")
            ax.set_ylabel("ms")
            ax.set_title(name)
            ax.legend()
            fig.tight_layout()
            fig.savefig(os.path.join(outdir, f"{name}_timeline.png"), dpi=130)
            plt.close(fig)
        except Exception as ex:
            print(f"(γράφημα παραλείφθηκε: {ex})")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="experiment_RECEIVED_*.csv")
    ap.add_argument("--sent", default=None, help="(προαιρετικό) experiment_SENT_*.csv (μόνο για ένα RECEIVED)")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    paths = []
    for f in a.files:
        paths += glob.glob(f) or [f]
    os.makedirs(a.out, exist_ok=True)

    rows = []
    for p in sorted(paths):
        if not os.path.exists(p):
            print(f"δεν βρέθηκε: {p}")
            continue
        r = analyze(p, a.sent if len(paths) == 1 else None, a.out)
        rows.append(r)
        print(f"\n=== {r['run']} ===")
        print(f"  νότες note_on: {r['note_on']}  (γραμμές {r['rows']}, εκτός ok: {r['dropped_or_nosync']})")
        print(f"  απώλεια: κενά seq = {r['seq_missing']}" +
              (f", χαμένες vs SENT = {r.get('lost_vs_sent')}" if 'lost_vs_sent' in r else ""))
        print(f"  transit_est_ms: min {r['transit_min']:.1f} | p50 {r['transit_p50']:.1f} | "
              f"p99 {r['transit_p99']:.1f} | p99.9 {r['transit_p999']:.1f} | max {r['transit_max']:.1f}")
        print(f"  jitter (p99-min) = {r['jitter_p99_min']:.1f} ms | IPDV std = {r['ipdv_std']:.1f} ms")
        print(f"  late% (late=1) = {r['late_pct']:.2f}% | lateness p50 {r['lateness_p50']:.1f} | "
              f"p99 {r['lateness_p99']:.1f} | max {r['lateness_max']:.1f} ms")
        print(f"  buffer delay_ms: min {r['delay_min']:.0f} | mean {r['delay_mean']:.1f} | max {r['delay_max']:.0f} "
              f"(βάση {r['base_ms']:.0f}, adaptive={r['adaptive']}, πάνω από τη βάση {r['pct_above_base']:.1f}% των νοτών)")
        print(f"  effective delay: mean {r['eff_mean']:.1f} | p95 {r['eff_p95']:.1f} | p99 {r['eff_p99']:.1f} ms")

    if rows:
        out = os.path.join(a.out, "summary_runs.csv")
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f"\nΓράφτηκε: {out}")


if __name__ == "__main__":
    sys.exit(main())
