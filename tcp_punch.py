# -*- coding: utf-8 -*-
"""
tcp_punch.py — P2P πάνω από ΠΡΑΓΜΑΤΙΚΟ TCP, με TCP hole punching (simultaneous open).

Πώς δουλεύει
------------
1. Η σύνδεση WebSocket του client με τον server έχει τοπική θύρα L. Το NAT την αντιστοιχεί σε μια δημόσια
   θύρα P, που τη βλέπει ο server (peername). Ο server στέλνει στον client το (IP, P) ("your_endpoint").
2. Οι δύο χρήστες ανταλλάσσουν μέσω server τα (δημόσιο IP:P, τοπικό IP:L) ("candidate" με tcp=true).
3. Και οι δύο ανοίγουν νέα TCP sockets δεμένα στην ΙΔΙΑ τοπική θύρα L (SO_REUSEADDR) και κάνουν
   connect() στη δημόσια διεύθυνση του άλλου, ταυτόχρονα και επαναλαμβανόμενα. Το πρώτο SYN ανοίγει τρύπα στο
   NAT του καθενός και το SYN του άλλου περνά (TCP simultaneous open). Παράλληλα ακούει (listen) στην ίδια
   θύρα, για την περίπτωση που ο άλλος προλάβει πρώτος.
4. Όταν στηθεί η σύνδεση, ανταλλάσσεται "hello" (όνομα χρήστη) και ξεκινά το TcpLink.

Το TcpLink έχει την ίδια διεπαφή με το RedundantLink (send, pending_events, snapshot, close, start,
broken, closed, break_info), οπότε το υπόλοιπο P2P (fallback στον server, reconnect, στατιστικά) δεν αλλάζει.
Δεν υπάρχει δικό μας ARQ: τη αξιοπιστία και τη σειρά τα δίνει το TCP. Τα app-level acks υπάρχουν ΜΟΝΟ για να
ξέρουμε ποια events εκκρεμούν όταν πέσει το link (τότε ξαναστέλνονται μέσω server).

Η τρύπα στο NAT ΔΕΝ είναι εγγυημένη (π.χ. symmetric NAT / CGNAT, firewall). Τότε το ζευγάρι μένει στον server.
"""
import asyncio
import json
import socket
import threading
import time
from collections import OrderedDict


# ------------------------------------------------------------------------------------------------
#  sockets
# ------------------------------------------------------------------------------------------------
def prepare_existing_socket(sock):
    """Το socket του WebSocket (ήδη συνδεδεμένο) πρέπει να έχει SO_REUSEADDR, ώστε να μπορούμε να δέσουμε
    νέα sockets στην ίδια τοπική θύρα (Linux/macOS). Στα Windows δεν χρειάζεται, αλλά δεν βλάπτει."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return True
    except Exception:
        return False


def connect_reusable(url, timeout=10.0):
    """Συνδέει (blocking) ένα socket στον server με SO_REUSEADDR ΠΡΙΝ το bind. Στα Windows, αλλιώς, δεν
    μπορεί να δεθεί δεύτερο socket στην ίδια τοπική θύρα (WinError 10013). Το socket δίνεται στο
    websockets.connect(..., sock=...). Επιστρέφει None αν αποτύχει (τότε γίνεται κανονική σύνδεση)."""
    from urllib.parse import urlparse
    u = urlparse(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "wss" else 80)
    last = None
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except Exception:
        return None
    for fam, typ, proto, _, addr in infos:
        s = socket.socket(fam, typ, proto)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.bind(("0.0.0.0", 0))
            s.settimeout(timeout)
            s.connect(addr)
            s.settimeout(None)
            return s
        except Exception as e:
            last = e
            s.close()
    return None


def _make_socket(local_port, timeout):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(timeout)
    s.bind(("0.0.0.0", local_port))
    return s


def prefer_new(me, peer, old_initiator, new_initiator):
    """Αν υπάρχουν δύο συνδέσεις με τον ίδιο χρήστη, κρατιέται αυτή που ξεκίνησε ο ΜΙΚΡΟΤΕΡΟΣ (αλφαβητικά)
    από τους δύο, ώστε και οι δύο πλευρές να διαλέξουν την ίδια. True = αντικατάστησε την παλιά."""
    pref = min(me, peer)
    return new_initiator == pref and old_initiator != pref


class TcpPuncher:
    """Ένας listener ανά παράθυρο δωματίου + threads που κάνουν connect() προς τους άλλους.
    Όλα τα callbacks εκτελούνται στο event loop (call_soon_threadsafe)."""

    def __init__(self, local_port, loop, on_inbound, log=print):
        self.local_port = local_port
        self.loop = loop
        self.on_inbound = on_inbound        # f(sock, addr)
        self.log = log
        self._stop = threading.Event()
        self._listener = None
        self._listen_error = None
        self._start_listener()

    # ---------- listener ----------
    def _start_listener(self):
        try:
            ls = _make_socket(self.local_port, 0.5)
            ls.listen(8)
            self._listener = ls
        except OSError as e:
            self._listen_error = e          # δεν είναι μοιραίο: μένει το connect προς τον άλλον
            self.log(f"⚠️ TCP punch: could not listen on port {self.local_port}: {e}")
            return
        threading.Thread(target=self._accept_loop, name="tcp-punch-accept", daemon=True).start()

    def _accept_loop(self):
        ls = self._listener
        while not self._stop.is_set():
            try:
                conn, addr = ls.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self.loop.call_soon_threadsafe(self.on_inbound, conn, addr)
            except RuntimeError:
                conn.close()
                return

    # ---------- connect ----------
    def connect(self, targets, on_outbound, stop_evt, deadline_s, attempt_s=1.0):
        """Επαναλαμβανόμενα connect() προς κάθε target [(ip, port)] από την τοπική θύρα. Σταματά όταν πετύχει
        κάποιο, όταν σηκωθεί το stop_evt ή περάσει το deadline_s. on_outbound(sock, target) στο event loop."""
        t = threading.Thread(target=self._connect_loop, name="tcp-punch-connect", daemon=True,
                             args=(list(targets), on_outbound, stop_evt, deadline_s, attempt_s))
        t.start()
        return t

    def _connect_loop(self, targets, on_outbound, stop_evt, deadline_s, attempt_s):
        end = time.monotonic() + deadline_s
        while not stop_evt.is_set() and not self._stop.is_set() and time.monotonic() < end:
            for tgt in targets:
                if stop_evt.is_set() or self._stop.is_set():
                    return
                s = None
                try:
                    s = _make_socket(self.local_port, attempt_s)
                    s.connect(tgt)
                except (socket.timeout, OSError):
                    if s is not None:
                        try:
                            s.close()
                        except OSError:
                            pass
                    time.sleep(0.15)        # RST/refused επιστρέφει αμέσως: μικρή παύση πριν το επόμενο SYN
                    continue
                if stop_evt.is_set():
                    s.close()
                    return
                try:
                    self.loop.call_soon_threadsafe(on_outbound, s, tgt)
                except RuntimeError:
                    s.close()
                return

    def close(self):
        self._stop.set()
        try:
            if self._listener is not None:
                self._listener.close()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------
#  link
# ------------------------------------------------------------------------------------------------
async def open_streams(sock):
    sock.setblocking(False)
    return await asyncio.open_connection(sock=sock)


async def handshake(reader, writer, me, sid, timeout=3.0):
    """Στέλνει hello και διαβάζει το hello του άλλου. Επιστρέφει (όνομα, sid) ή None."""
    writer.write((json.dumps({"k": "h", "u": me, "sid": sid}, separators=(",", ":")) + "\n").encode())
    try:
        line = await asyncio.wait_for(reader.readline(), timeout)
        msg = json.loads(line.decode("utf-8", "ignore"))
    except Exception:
        return None
    if msg.get("k") != "h" or not msg.get("u"):
        return None
    return msg["u"], msg.get("sid")


class TcpLink:
    """MIDI events πάνω από μία TCP σύνδεση (γραμμές JSON). Διεπαφή ίδια με το RedundantLink."""
    HEARTBEAT_S = 1.0
    DEAD_AFTER_S = 5.0
    ACK_DELAY = 0.003
    CLOCK_SYNC_S = 3.0          # κάθε πόσο στέλνεται αίτημα συγχρονισμού ρολογιού προς τον peer
    CLOCK_WINDOW = 8            # δείγματα από τα οποία κρατιέται αυτό με το μικρότερο RTT
    CLOCK_BURST = 4             # αρχικά αιτήματα, ανά 0.25 s, ώστε να υπάρχει offset αμέσως μετά τη σύνδεση

    def __init__(self, reader, writer, deliver, on_broken=None, max_age=3.0, clock=time.perf_counter, on_clock=None):
        self.reader = reader
        self.writer = writer
        self.deliver = deliver
        self.on_broken = on_broken
        self.on_clock = on_clock            # callback(rtt_s, offset_s) σε κάθε νέο δείγμα συγχρονισμού ρολογιού
        self.max_age = max_age
        self.now = clock
        self.broken = False
        self.closed = False
        self.break_info = None
        self.next_seq = 1
        self.unacked = OrderedDict()        # seq -> [ev, t_sent]
        self.rx_last = 0
        self.srtt = None
        self.last_rx = None
        self.last_ack = None
        self.clock_samples = []             # (rtt_s, offset_s): offset = ρολόι_peer − δικό_μου
        self._ack_handle = None
        self._tasks = []
        self.stats = {"sent": 0, "retx": 0, "dups": 0, "delivered": 0, "acks_tx": 0, "hedged": 0}

    # ---------- αποστολή ----------
    def _write(self, obj):
        self.writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def send(self, ev):
        if self.closed or self.broken:
            return False
        seq = self.next_seq
        self.next_seq += 1
        self.unacked[seq] = [ev, self.now()]
        self.stats["sent"] += 1
        try:
            self._write({"k": "d", "s": seq, "e": ev})
        except Exception as e:
            self._break(f"write failed: {e}")
            return False
        return True

    def _send_ack(self):
        self._ack_handle = None
        if self.closed or self.broken:
            return
        self.stats["acks_tx"] += 1
        try:
            self._write({"k": "a", "a": self.rx_last})
        except Exception as e:
            self._break(f"write failed: {e}")

    def _schedule_ack(self):
        if self._ack_handle is None:
            self._ack_handle = asyncio.get_event_loop().call_later(self.ACK_DELAY, self._send_ack)

    # ---------- λήψη ----------
    async def _read_loop(self):
        try:
            while not self.closed and not self.broken:
                line = await self.reader.readline()
                if not line:
                    self._break("connection closed by the other side")
                    return
                now = self.now()
                self.last_rx = now
                try:
                    pkt = json.loads(line)
                except Exception:
                    continue
                k = pkt.get("k")
                if k == "d":
                    s = pkt.get("s", 0)
                    if s <= self.rx_last:
                        self.stats["dups"] += 1
                    else:
                        self.rx_last = s
                        self.stats["delivered"] += 1
                        try:
                            self.deliver(pkt.get("e"))
                        except Exception as e:
                            print("⚠️ TCP P2P deliver error:", e)
                    self._schedule_ack()
                elif k == "a":
                    a = pkt.get("a", 0)
                    for s in [s for s in self.unacked if s <= a]:
                        ent = self.unacked.pop(s)
                        self.last_ack = now
                        rtt = now - ent[1]
                        self.srtt = rtt if self.srtt is None else 0.875 * self.srtt + 0.125 * rtt
                elif k == "p":              # heartbeat: απάντηση με ack
                    self._schedule_ack()
                elif k == "ts":             # αίτημα συγχρονισμού ρολογιού: απάντηση αμέσως με το ρολόι μου
                    self._write({"k": "tr", "t0": pkt.get("t0"), "tb": self.now()})
                elif k == "tr":             # απάντηση: Cristian/NTP πάνω στο ίδιο link που κουβαλά τις νότες
                    t0, tb = pkt.get("t0"), pkt.get("tb")
                    if t0 is not None and tb is not None:
                        rtt = now - t0
                        if rtt >= 0:
                            self.clock_samples.append((rtt, tb - (t0 + rtt / 2.0)))
                            del self.clock_samples[:-self.CLOCK_WINDOW]
                            if self.on_clock is not None:
                                try:
                                    self.on_clock(rtt, self.peer_offset)
                                except Exception as e:
                                    print("⚠️ TCP P2P on_clock error:", e)
        except asyncio.CancelledError:
            pass
        except (ConnectionError, OSError) as e:
            self._break(f"read failed: {e}")
        except Exception as e:
            self._break(f"read error: {e}")

    # ---------- επίβλεψη ----------
    async def _watch_loop(self):
        last_hb = self.now()
        last_cs = None
        burst = self.CLOCK_BURST
        try:
            while not self.closed and not self.broken:
                await asyncio.sleep(0.01)
                now = self.now()
                if self.unacked:
                    first = next(iter(self.unacked.values()))
                    if now - first[1] > self.max_age:
                        self._break(f"oldest event {1000 * (now - first[1]):.0f} ms without ack")
                        return
                if now - last_hb >= self.HEARTBEAT_S:
                    last_hb = now
                    try:
                        self._write({"k": "p"})
                    except Exception as e:
                        self._break(f"write failed: {e}")
                        return
                gap = 0.25 if burst > 0 else self.CLOCK_SYNC_S
                if last_cs is None or now - last_cs >= gap:
                    last_cs = now
                    burst = max(0, burst - 1)
                    try:
                        self._write({"k": "ts", "t0": now})
                    except Exception as e:
                        self._break(f"write failed: {e}")
                        return
                if self.last_rx is not None and now - self.last_rx > self.DEAD_AFTER_S:
                    self._break(f"nothing received for {1000 * (now - self.last_rx):.0f} ms")
                    return
        except asyncio.CancelledError:
            pass

    def start(self):
        self.last_rx = self.now()
        self._tasks = [asyncio.ensure_future(self._read_loop()), asyncio.ensure_future(self._watch_loop())]

    # ---------- στατιστικά / κατάσταση ----------
    def snapshot(self):
        now = self.now()
        oldest = (now - next(iter(self.unacked.values()))[1]) * 1000.0 if self.unacked else 0.0
        try:
            wbuf = self.writer.transport.get_write_buffer_size()
        except Exception:
            wbuf = None
        return {"unacked": len(self.unacked), "oldest_ms": oldest,
                "srtt_ms": None if self.srtt is None else self.srtt * 1000.0,
                "since_rx_ms": None if self.last_rx is None else (now - self.last_rx) * 1000.0,
                "since_ack_ms": None if self.last_ack is None else (now - self.last_ack) * 1000.0,
                "wbuf": wbuf,
                "clock_off_ms": None if self.peer_offset is None else self.peer_offset * 1000.0,
                "clock_rtt_ms": None if self.peer_rtt is None else self.peer_rtt * 1000.0, **self.stats}

    @property
    def peer_offset(self):
        """Offset του ρολογιού του peer (peer − εγώ, σε s) από το δείγμα με το μικρότερο RTT, ή None αν δεν υπάρχει."""
        if not self.clock_samples:
            return None
        return min(self.clock_samples, key=lambda x: x[0])[1]

    @property
    def peer_rtt(self):
        """Μικρότερο RTT (s) των τελευταίων δειγμάτων συγχρονισμού, ή None."""
        if not self.clock_samples:
            return None
        return min(self.clock_samples, key=lambda x: x[0])[0]

    def pending_events(self):
        return [v[0] for v in self.unacked.values()]

    def _shutdown_streams(self):
        for t in self._tasks:
            if t is not asyncio.current_task():
                t.cancel()
        if self._ack_handle is not None:
            self._ack_handle.cancel()
            self._ack_handle = None
        try:
            self.writer.close()
        except Exception:
            pass

    def _break(self, reason):
        if self.broken or self.closed:
            return
        self.break_info = {**self.snapshot(), "reason": reason}
        self.broken = True
        pending = [v[0] for v in self.unacked.values()]
        self.unacked.clear()
        self._shutdown_streams()
        if self.on_broken:
            try:
                self.on_broken(pending)
            except Exception as e:
                print("⚠️ TCP P2P on_broken error:", e)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._shutdown_streams()
