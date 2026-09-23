#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_experiment.py
=====================
Ανάλυση των πειραματικών CSV της εφαρμογής MIDIRooms.

Διαβάζει ένα ή περισσότερα experiment_RECEIVED_*.csv αρχεία (και προαιρετικά
τα αντίστοιχα experiment_SENT_*.csv για ανίχνευση απώλειας) και υπολογίζει
τις μετρικές αξιολόγησης του συστήματος:

  - Latency         : mean, median, min, max, std, p95, p99
  - Playback jitter  : std του latency + mean μεταβολή διαδοχικών events
  - Note loss        : events που στάλθηκαν αλλά δεν ελήφθησαν (κενά στα seq)
  - Clock uncertainty: μέσο server_jitter_ms (αβεβαιότητα των μετρήσεων)

Παράγει:
  - summary_statistics.csv  : όλες οι μετρικές σε έναν πίνακα
  - latency_histogram.png   : κατανομή του latency
  - latency_over_time.png   : πώς εξελίχθηκε το latency στη διάρκεια
  - jitter_over_time.png    : μεταβλητότητα (playback jitter) στη διάρκεια

Χρήση:
    python analyze_experiment.py experiment_RECEIVED_akos_20260101_120000.csv
    python analyze_experiment.py *.csv          (πολλαπλά αρχεία)
    python analyze_experiment.py received.csv --sent sent.csv   (με ανίχνευση loss)

Απαιτούμενες βιβλιοθήκες:
    pip install pandas numpy matplotlib
"""

import sys
import os
import glob
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # χωρίς GUI, γράφει κατευθείαν σε αρχεία
import matplotlib.pyplot as plt


# ----------------------------------------------------------------------------
# Φόρτωση & έλεγχος δεδομένων
# ----------------------------------------------------------------------------
def load_received(paths):
    """Φορτώνει και ενώνει ένα ή περισσότερα RECEIVED csv."""
    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p)
        except Exception as e:
            print(f"⚠️  Παράλειψη {p}: {e}")
            continue
        if "latency_ms" not in df.columns:
            print(f"⚠️  Το {p} δεν έχει στήλη latency_ms — παραλείπεται.")
            continue
        df["source_file"] = os.path.basename(p)
        frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


# ----------------------------------------------------------------------------
# Υπολογισμός μετρικών
# ----------------------------------------------------------------------------
def compute_metrics(df, sent_df=None):
    """Επιστρέφει dict με όλες τις μετρικές αξιολόγησης."""
    lat = pd.to_numeric(df["latency_ms"], errors="coerce").dropna().to_numpy()

    metrics = {}

    # ---- Latency ----
    metrics["events_received"] = int(len(lat))
    metrics["latency_mean_ms"] = float(np.mean(lat))
    metrics["latency_median_ms"] = float(np.median(lat))
    metrics["latency_min_ms"] = float(np.min(lat))
    metrics["latency_max_ms"] = float(np.max(lat))
    metrics["latency_std_ms"] = float(np.std(lat, ddof=1)) if len(lat) > 1 else 0.0
    metrics["latency_p95_ms"] = float(np.percentile(lat, 95))
    metrics["latency_p99_ms"] = float(np.percentile(lat, 99))

    # ---- Playback jitter ----
    # (1) συνολική μεταβλητότητα = τυπική απόκλιση του latency
    metrics["playback_jitter_std_ms"] = metrics["latency_std_ms"]
    # (2) μεταβλητότητα μεταξύ διαδοχικών events — αυτό "νιώθει" ρυθμικά ο μουσικός
    if len(lat) > 1:
        consecutive = np.abs(np.diff(lat))
        metrics["playback_jitter_consecutive_ms"] = float(np.mean(consecutive))
    else:
        metrics["playback_jitter_consecutive_ms"] = 0.0

    # ---- Clock uncertainty (αβεβαιότητα μετρήσεων) ----
    if "server_jitter_ms" in df.columns:
        sj = pd.to_numeric(df["server_jitter_ms"], errors="coerce").dropna().to_numpy()
        metrics["clock_jitter_mean_ms"] = float(np.mean(sj)) if len(sj) else 0.0
    else:
        metrics["clock_jitter_mean_ms"] = float("nan")

    if "server_offset_ms" in df.columns:
        so = pd.to_numeric(df["server_offset_ms"], errors="coerce").dropna().to_numpy()
        metrics["clock_offset_mean_ms"] = float(np.mean(so)) if len(so) else 0.0
    else:
        metrics["clock_offset_mean_ms"] = float("nan")

    # ---- Note / packet loss ----
    if sent_df is not None and "seq" in sent_df.columns and "seq" in df.columns:
        sent_seqs = set(pd.to_numeric(sent_df["seq"], errors="coerce").dropna().astype(int))
        recv_seqs = set(pd.to_numeric(df["seq"], errors="coerce").dropna().astype(int))
        lost = sent_seqs - recv_seqs
        metrics["events_sent"] = int(len(sent_seqs))
        metrics["events_lost"] = int(len(lost))
        metrics["loss_rate_percent"] = (
            100.0 * len(lost) / len(sent_seqs) if sent_seqs else 0.0
        )
    else:
        # χωρίς SENT: ανίχνευση κενών μέσα στην ακολουθία seq των ληφθέντων
        if "seq" in df.columns:
            seqs = pd.to_numeric(df["seq"], errors="coerce").dropna().astype(int).to_numpy()
            if len(seqs) > 1:
                seqs_sorted = np.sort(np.unique(seqs))
                expected = seqs_sorted[-1] - seqs_sorted[0] + 1
                gaps = int(expected - len(seqs_sorted))
                metrics["events_sent"] = int(expected)
                metrics["events_lost"] = gaps
                metrics["loss_rate_percent"] = 100.0 * gaps / expected if expected else 0.0
            else:
                metrics["events_sent"] = len(seqs)
                metrics["events_lost"] = 0
                metrics["loss_rate_percent"] = 0.0
        else:
            metrics["events_sent"] = float("nan")
            metrics["events_lost"] = float("nan")
            metrics["loss_rate_percent"] = float("nan")

    return metrics


# ----------------------------------------------------------------------------
# Γραφήματα
# ----------------------------------------------------------------------------
def plot_latency_histogram(df, out_path, ept=30.0):
    lat = pd.to_numeric(df["latency_ms"], errors="coerce").dropna().to_numpy()
    plt.figure(figsize=(9, 5.5))
    plt.hist(lat, bins=40, color="#4C72B0", edgecolor="white", alpha=0.85)
    plt.axvline(np.mean(lat), color="#C44E52", linestyle="--", linewidth=2,
                label=f"Mean = {np.mean(lat):.1f} ms")
    plt.axvline(np.median(lat), color="#55A868", linestyle="--", linewidth=2,
                label=f"Median = {np.median(lat):.1f} ms")
    plt.axvline(ept, color="#8172B3", linestyle=":", linewidth=2,
                label=f"EPT threshold = {ept:.0f} ms")
    plt.xlabel("End-to-end latency (ms)")
    plt.ylabel("Αριθμός MIDI events")
    plt.title("Κατανομή end-to-end latency")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_latency_over_time(df, out_path):
    lat = pd.to_numeric(df["latency_ms"], errors="coerce").to_numpy()
    idx = np.arange(len(lat))
    plt.figure(figsize=(11, 5.5))
    plt.plot(idx, lat, color="#4C72B0", linewidth=0.9, alpha=0.8)
    mean_val = np.nanmean(lat)
    plt.axhline(mean_val, color="#C44E52", linestyle="--", linewidth=1.5,
                label=f"Mean = {mean_val:.1f} ms")
    plt.xlabel("Αριθμός event (χρονική σειρά)")
    plt.ylabel("Latency (ms)")
    plt.title("Latency στη διάρκεια της συνεδρίας")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_jitter_over_time(df, out_path):
    """Μεταβλητότητα (playback jitter) = απόκλιση κάθε event από το μέσο latency."""
    lat = pd.to_numeric(df["latency_ms"], errors="coerce").to_numpy()
    mean_val = np.nanmean(lat)
    deviation = lat - mean_val
    idx = np.arange(len(lat))
    plt.figure(figsize=(11, 5.5))
    plt.plot(idx, deviation, color="#55A868", linewidth=0.9, alpha=0.8)
    plt.axhline(0, color="#333333", linewidth=1)
    std_val = np.nanstd(lat, ddof=1) if len(lat) > 1 else 0.0
    plt.axhline(std_val, color="#C44E52", linestyle=":", linewidth=1.3,
                label=f"+1σ = {std_val:.1f} ms")
    plt.axhline(-std_val, color="#C44E52", linestyle=":", linewidth=1.3)
    plt.xlabel("Αριθμός event (χρονική σειρά)")
    plt.ylabel("Απόκλιση από μέσο latency (ms)")
    plt.title("Playback jitter — μεταβλητότητα latency γύρω από το μέσο")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


# ----------------------------------------------------------------------------
# Εκτύπωση & αποθήκευση
# ----------------------------------------------------------------------------
def print_report(metrics):
    print("\n" + "=" * 55)
    print("        ΑΠΟΤΕΛΕΣΜΑΤΑ ΑΝΑΛΥΣΗΣ — MIDIRooms")
    print("=" * 55)

    print("\n--- LATENCY (end-to-end) ---")
    print(f"  Events received : {metrics['events_received']}")
    print(f"  Mean            : {metrics['latency_mean_ms']:.2f} ms")
    print(f"  Median          : {metrics['latency_median_ms']:.2f} ms")
    print(f"  Min / Max       : {metrics['latency_min_ms']:.2f} / {metrics['latency_max_ms']:.2f} ms")
    print(f"  Std deviation   : {metrics['latency_std_ms']:.2f} ms")
    print(f"  95th percentile : {metrics['latency_p95_ms']:.2f} ms")
    print(f"  99th percentile : {metrics['latency_p99_ms']:.2f} ms")

    print("\n--- PLAYBACK JITTER ---")
    print(f"  Std (συνολική μεταβλητότητα)      : {metrics['playback_jitter_std_ms']:.2f} ms")
    print(f"  Mean μεταβολή διαδοχικών events   : {metrics['playback_jitter_consecutive_ms']:.2f} ms")

    print("\n--- NOTE / PACKET LOSS ---")
    print(f"  Events sent     : {metrics['events_sent']}")
    print(f"  Events lost     : {metrics['events_lost']}")
    print(f"  Loss rate       : {metrics['loss_rate_percent']:.3f} %")

    print("\n--- CLOCK UNCERTAINTY (αβεβαιότητα μετρήσεων) ---")
    print(f"  Mean clock offset : {metrics['clock_offset_mean_ms']:.2f} ms")
    print(f"  Mean clock jitter : {metrics['clock_jitter_mean_ms']:.2f} ms")
    print("=" * 55 + "\n")


def save_summary_csv(metrics, out_path):
    order = [
        "events_received", "events_sent", "events_lost", "loss_rate_percent",
        "latency_mean_ms", "latency_median_ms", "latency_min_ms", "latency_max_ms",
        "latency_std_ms", "latency_p95_ms", "latency_p99_ms",
        "playback_jitter_std_ms", "playback_jitter_consecutive_ms",
        "clock_offset_mean_ms", "clock_jitter_mean_ms",
    ]
    rows = [(k, metrics.get(k, "")) for k in order]
    pd.DataFrame(rows, columns=["metric", "value"]).to_csv(out_path, index=False)
    print(f"💾 Summary αποθηκεύτηκε: {out_path}")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Ανάλυση πειραματικών CSV MIDIRooms")
    parser.add_argument("received", nargs="+",
                        help="Ένα ή περισσότερα experiment_RECEIVED_*.csv")
    parser.add_argument("--sent", nargs="*", default=None,
                        help="(προαιρετικό) experiment_SENT_*.csv για ανίχνευση loss")
    parser.add_argument("--ept", type=float, default=30.0,
                        help="EPT threshold για τα γραφήματα (default 30 ms)")
    parser.add_argument("--outdir", default=".",
                        help="Φάκελος εξόδου για γραφήματα/summary")
    args = parser.parse_args()

    # ανάπτυξη wildcards (για Windows cmd που δεν το κάνει μόνο του)
    recv_paths = []
    for pat in args.received:
        expanded = glob.glob(pat)
        recv_paths.extend(expanded if expanded else [pat])

    df = load_received(recv_paths)
    if df is None or df.empty:
        print("❌ Δεν φορτώθηκαν έγκυρα RECEIVED δεδομένα.")
        sys.exit(1)

    sent_df = None
    if args.sent:
        sent_paths = []
        for pat in args.sent:
            expanded = glob.glob(pat)
            sent_paths.extend(expanded if expanded else [pat])
        sent_frames = []
        for p in sent_paths:
            try:
                sent_frames.append(pd.read_csv(p))
            except Exception as e:
                print(f"⚠️  Παράλειψη SENT {p}: {e}")
        if sent_frames:
            sent_df = pd.concat(sent_frames, ignore_index=True)

    os.makedirs(args.outdir, exist_ok=True)

    metrics = compute_metrics(df, sent_df)
    print_report(metrics)
    save_summary_csv(metrics, os.path.join(args.outdir, "summary_statistics.csv"))

    plot_latency_histogram(df, os.path.join(args.outdir, "latency_histogram.png"), ept=args.ept)
    plot_latency_over_time(df, os.path.join(args.outdir, "latency_over_time.png"))
    plot_jitter_over_time(df, os.path.join(args.outdir, "jitter_over_time.png"))
    print("📊 Γραφήματα αποθηκεύτηκαν: latency_histogram.png, latency_over_time.png, jitter_over_time.png")


if __name__ == "__main__":
    main()
