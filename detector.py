"""Din pachete CSI -> metrica de miscare -> stare -> SQLite.

DOUA metrici, combinate, ambele calibrate din sesiunea `gol`:

1. DISTANTA DE FORMA CSI. Mediem amplitudinile pe ~0.5s si comparam forma
   rezultata cu cea de acum ~1s (corelatie pe vectori centrati). Un om care se
   misca schimba multipath-ul, deci forma se decoreleaza; o camera goala are
   forma stabila.

   Medierea nu e optionala. Zgomotul de la pachet la pachet e la fel de mare in
   camera goala ca in timpul miscarii, si intr-o fereastra de o secunda acopera
   complet semnalul util. Masurat pe date reale: fara mediere separarea e 1.3x
   (inutilizabil), cu mediere 9-12x.

2. DEVIATIA RSSI pe ~4s. Mai lenta si mai grosiera, dar imuna la zgomotul de
   mai sus si, pe datele reale, singura care separa complet cele doua situatii.

Scorul final e cat de mult depaseste oricare din cele doua propriul prag, deci
1.0 inseamna exact la limita. Asta face graficul din dashboard citibil fara
sa stii ce inseamna numerele brute.

ATENTIE la interpretare: metricile astea vad MISCARE, nu PREZENTA. O persoana
complet nemiscata si o camera goala dau acelasi semnal. De aceea starile sunt
denumite dupa miscare, iar alerta e "nicio miscare de X minute".
"""

import json
import sqlite3
import time
from collections import deque, namedtuple
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

CALIBRATION_PATH = Path(__file__).parent / 'calibration.json'
DB_PATH = Path(__file__).parent / 'data' / 'monitor.db'

# Stari posibile. SENZOR_OFFLINE e separata intentionat: lipsa pachetelor nu
# trebuie confundata niciodata cu liniste in camera.
CALIBRARE = 'CALIBRARE'
MISCARE = 'MISCARE'
LINISTE = 'LINISTE'
INACTIVITATE = 'INACTIVITATE'
SENZOR_OFFLINE = 'SENZOR_OFFLINE'

Update = namedtuple('Update', 't metric state pkt_rate rssi csi_dist rssi_std')


@dataclass
class Config:
    avg_sec: float = 0.5           # mediere, ca sa stingem zgomotul per pachet
    lag_sec: float = 1.0           # la ce distanta in timp comparam forma
    rssi_sec: float = 4.0          # fereastra pentru deviatia RSSI
    warmup_packets: int = 200      # pachete pentru aflarea subpurtatoarelor valide
    min_packets: int = 5           # sub atat, o fereastra nu e statistic utila
    emit_interval: float = 0.25    # cat de des recalculam

    # Cele patru praguri sunt calibrate de analyze.py din sesiunea `gol`.
    # Pragul de iesire e ancorat tot in zgomotul camerei goale, nu exprimat ca
    # fractie din pragul de intrare: altfel poate ajunge sub nivelul de zgomot
    # si starea ramane blocata pe MISCARE la nesfarsit, iar alerta de
    # inactivitate nu se declanseaza niciodata.
    csi_threshold: float = 0.010
    csi_exit: float = 0.006
    rssi_threshold: float = 1.50
    rssi_exit: float = 0.95

    # Cat trebuie sa se tina scorul peste prag ca sa declaram MISCARE.
    # Cumpara specificitate cu latenta, deci valoarea nu se ghiceste: masurata
    # pe 28 min de camera goala plus doua sesiuni cu om, 0.5s iese cel mai bun
    # compromis pe toate axele deodata (zero alarme false, latenta mediana 2.5s,
    # cea mai lunga pauza in timpul activitatii 5s). Peste 1s latenta creste
    # fara sa castigam nimic; sub 0.5s pragul trebuie urcat de patru ori ca sa
    # ramana fara alarme false.
    min_motion_sec: float = 0.5

    # Cat timp ramanem in MISCARE dupa ultima fereastra peste prag. Fara asta,
    # un scor care pluteste in jurul pragului comuta starea la fiecare doua
    # secunde si umple istoricul cu sute de tranzitii ilizibile. Acelasi
    # principiu ca la un senzor PIR care tine lumina aprinsa dupa ultima
    # miscare. Nu intarzie alerta de inactivitate: cronometrul de liniste
    # numara de la ultima miscare reala, nu de la schimbarea starii.
    # Masurat pe 5 min de activitate continua: 0s da 48 de tranzitii, 3s da 2.
    # Alegem cea mai mica valoare cu marja, nu una mai mare -- fiecare secunda
    # in plus intarzie trecerea pe galben dupa ce omul iese din camera.
    motion_hold_sec: float = 5.0

    # Sub cat dB fata de mediana ferestrei consideram un pachet nesigur.
    fade_db: float = 8.0

    quiet_to_alert_sec: float = 300.0
    offline_sec: float = 5.0

    @property
    def history_sec(self):
        return max(self.lag_sec + self.avg_sec, self.rssi_sec) + 1.0


def load_config():
    cfg = Config()
    if CALIBRATION_PATH.exists():
        saved = json.loads(CALIBRATION_PATH.read_text(encoding='utf-8'))
        for k, v in saved.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


def save_config(cfg):
    CALIBRATION_PATH.write_text(json.dumps(asdict(cfg), indent=2), encoding='utf-8')


def robust_spread(values, clip_db):
    """Deviatia standard a valorilor RSSI, dupa limitarea la +/- clip_db fata
    de mediana.

    Cam 5% din pachete sosesc cu 15-20 dB sub restul. Fara limitare, un singur
    astfel de pachet umfla deviatia de zece ori si arata ca miscare. Nu folosim
    MAD in locul deviatiei: RSSI vine cuantizat in pasi de 1 dB, iar intr-o
    camera linistita aproape toate pachetele au aceeasi valoare -- MAD ar iesi
    exact zero, iar pragul calibrat pe el ar deveni zero.
    """
    v = np.asarray(values, dtype=float)
    med = np.median(v)
    return float(np.std(np.clip(v, med - clip_db, med + clip_db)))


def shape_distance(a, b):
    """1 - corelatia intre doua forme de spectru. 0 = identice, 1 = fara legatura.

    Centram pe medie inainte de comparatie: fara asta, componenta constanta
    mare a spectrului domina rezultatul si orice doua pachete par identice.
    """
    a = a - a.mean()
    b = b - b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(1.0 - np.dot(a, b) / (na * nb))


class StateMachine:
    """Scor -> stare. Separata de detector ca analyze.py sa poata rula alte
    praguri peste aceleasi metrici, fara sa reciteasca fisierele."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.state = CALIBRARE
        self.last_motion_t = None
        self._above_since = None
        self._last_above = None

    def step(self, now, enter_score, exit_score):
        if self.state == MISCARE:
            # Histerezis: odata intrati in MISCARE, iesim abia sub un prag mai mic.
            above = exit_score > 1.0
            if above:
                self._last_above = now
            else:
                self._above_since = None
            # ...si chiar si atunci, abia dupa o perioada de mentinere.
            moving = above or (self._last_above is not None
                               and now - self._last_above < self.cfg.motion_hold_sec)
        elif enter_score > 1.0:
            # Persistenta: pragul depasit o clipa nu e destul.
            if self._above_since is None:
                self._above_since = now
            moving = (now - self._above_since) >= self.cfg.min_motion_sec
            if moving:
                self._last_above = now
        else:
            self._above_since = None
            moving = False

        if moving:
            if self._last_above == now:
                self.last_motion_t = now
            return self._set(MISCARE)

        if self.last_motion_t is None:
            self.last_motion_t = now
        quiet = now - self.last_motion_t
        return self._set(INACTIVITATE if quiet >= self.cfg.quiet_to_alert_sec else LINISTE)

    def _set(self, state):
        changed = state != self.state
        self.state = state
        return state, changed


class MotionDetector:
    """Alimenteaza-l cu Packet-uri; intoarce (Update, s_a_schimbat_starea).

    Foloseste exclusiv timpul din pachet, niciodata ceasul de perete, ca sa dea
    exact acelasi rezultat live si la redarea unei sesiuni inregistrate.
    """

    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self._buf = deque()          # (t, amp mascat, rssi)
        self._rate_buf = deque()
        self._warmup = []
        self._mask = None
        self._n_sub = None
        self._last_emit = 0.0
        self._last_packet_t = None
        self._last_rssi = None
        self._sm = StateMachine(self.cfg)

    @property
    def state(self):
        return self._sm.state

    # -- fluxul de date ----------------------------------------------------

    def feed(self, pkt):
        self._last_packet_t = pkt.t
        self._last_rssi = pkt.rssi

        self._rate_buf.append(pkt.t)
        while self._rate_buf and pkt.t - self._rate_buf[0] > 5.0:
            self._rate_buf.popleft()

        if self._mask is None:
            self._warmup.append(pkt.amp)
            if len(self._warmup) < self.cfg.warmup_packets:
                return None
            self._finish_warmup()
            return None

        if len(pkt.amp) != self._n_sub:
            return None  # alt numar de LTF-uri in pachet; il ignoram

        self._buf.append((pkt.t, pkt.amp[self._mask], float(pkt.rssi)))
        while self._buf and pkt.t - self._buf[0][0] > self.cfg.history_sec:
            self._buf.popleft()

        if pkt.t - self._last_emit < self.cfg.emit_interval:
            return None
        self._last_emit = pkt.t
        return self._emit(pkt.t)

    def tick(self, now):
        """De apelat periodic in modul live, ca sa prinda senzorul cazut chiar
        si atunci cand nu mai vine niciun pachet care sa declanseze feed()."""
        if self._last_packet_t is None:
            return None
        if now - self._last_packet_t > self.cfg.offline_sec:
            return self._transition(now, SENZOR_OFFLINE)
        return None

    # -- calcul ------------------------------------------------------------

    def _finish_warmup(self):
        """Alegem subpurtatoarele utile din date, nu din tabele de indici
        codate per cip:
          - subpurtatoarele nule si de garda sunt exact zero;
          - primul indice e marcat `first_word_invalid` de firmware si are
            constant o amplitudine aberanta.

        Documentatia ESP-IDF (api-guides/wifi.rst) arata ca pentru un pachet HT
        pe 40 MHz cu canal secundar, cele 192 de subpurtatoare sunt LLTF pe
        indicii 0-63 (doar cei 20 MHz primari) si HT-LTF pe 64-191 (toti cei
        40 MHz, amplitudine aproape dubla).

        Pentru MISCARE pastram doar LLTF. Nu din principiu, ci masurat: cu
        HT-LTF inclus, zgomotul de fond urca de la 0.0002 la 0.0005, iar
        latenta de detectie de la 2.5s la 4.9s, peste pragul de 5s. HT-LTF are
        amplitudine mai mare dar si zgomot relativ mai mare, iar metrica de
        forma nu are cum sa-l medieze.

        Detectia de respiratie face invers si foloseste HT-LTF (vezi
        breathing.py): acolo FFT-ul pe doua minute scoate componenta periodica
        din acelasi zgomot, iar banda dubla da mai multe subpurtatoare
        independente care sa confirme aceeasi frecventa.
        """
        lengths = [len(a) for a in self._warmup]
        self._n_sub = max(set(lengths), key=lengths.count)
        stack = np.stack([a for a in self._warmup if len(a) == self._n_sub])
        self._warmup = []

        mask = (stack > 0).mean(axis=0) > 0.9
        mask[0] = False
        if self._n_sub > 64:
            mask[64:] = False        # doar LLTF, vezi motivul de mai sus
        self._mask = mask
        if mask.sum() < 8:
            raise RuntimeError(
                f'Doar {mask.sum()} subpurtatoare utile -- date CSI invalide.')

    def _avg_shape(self, t_end):
        """Forma medie pe fereastra, fara pachetele primite anormal de slab.

        Cam 5% din pachete sosesc cu 15-20 dB sub restul. Amplitudinea lor e de
        cateva ori mai mica, deci deformeaza media intregii ferestre -- masurat
        pe camera goala, exact asta producea varfurile care aratau ca miscare.
        Le eliminam inainte de mediere; CSI-ul lor nu e informatie despre
        camera, ci despre o receptie ratata.
        """
        span = self.cfg.avg_sec
        sel = [(a, r) for t, a, r in self._buf if t_end - span < t <= t_end]
        if len(sel) < self.cfg.min_packets:
            return None
        rssis = np.array([r for _, r in sel])
        keep = rssis >= np.median(rssis) - self.cfg.fade_db
        if keep.sum() < self.cfg.min_packets:
            return None
        return np.mean([a for (a, _), k in zip(sel, keep) if k], axis=0)

    def _emit(self, now):
        cfg = self.cfg
        # Nu raportam nimic pana nu s-a umplut si cea mai lunga fereastra.
        # Altfel primele secunde se calculeaza pe cateva pachete si produc
        # valori aberante, care arata ca miscare desi camera e goala.
        if not self._buf or self._buf[-1][0] - self._buf[0][0] < cfg.rssi_sec:
            return None

        recent = self._avg_shape(now)
        older = self._avg_shape(now - cfg.lag_sec)
        if recent is None or older is None:
            return None

        csi_dist = shape_distance(recent, older)

        rssis = [r for t, _, r in self._buf if now - cfg.rssi_sec < t <= now]
        rssi_std = (robust_spread(rssis, cfg.fade_db)
                    if len(rssis) >= cfg.min_packets else 0.0)

        # max(prag, 1e-9): o calibrare degenerata nu trebuie sa dea ZeroDivision
        enter = max(csi_dist / max(cfg.csi_threshold, 1e-9),
                    rssi_std / max(cfg.rssi_threshold, 1e-9))
        exit_ = max(csi_dist / max(cfg.csi_exit, 1e-9),
                    rssi_std / max(cfg.rssi_exit, 1e-9))

        state, changed = self._sm.step(now, enter, exit_)
        return Update(now, enter, state, self.packet_rate, self._last_rssi,
                      csi_dist, rssi_std), changed

    def _transition(self, now, state):
        changed = state != self._sm.state
        self._sm.state = state
        return Update(now, None, state, self.packet_rate, self._last_rssi,
                      None, None), changed

    @property
    def packet_rate(self):
        if len(self._rate_buf) < 2:
            return 0.0
        span = self._rate_buf[-1] - self._rate_buf[0]
        return (len(self._rate_buf) - 1) / span if span > 0 else 0.0


class Store:
    """Istoric pe disc: tranzitiile de stare si metrica esantionata."""

    def __init__(self, path=DB_PATH):
        Path(path).parent.mkdir(exist_ok=True)
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, ts REAL NOT NULL,
                state_from TEXT, state_to TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS samples (
                ts REAL NOT NULL, metric REAL, pkt_rate REAL, rssi INTEGER,
                state TEXT, csi_dist REAL, rssi_std REAL);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
        """)
        self.con.commit()
        self._last_sample_t = 0.0

    def event(self, ts, state_from, state_to):
        self.con.execute('INSERT INTO events (ts, state_from, state_to) VALUES (?,?,?)',
                         (ts, state_from, state_to))
        self.con.commit()

    def sample(self, upd, min_interval=1.0):
        if upd.t - self._last_sample_t < min_interval:
            return
        self._last_sample_t = upd.t
        self.con.execute(
            'INSERT INTO samples (ts, metric, pkt_rate, rssi, state, csi_dist, rssi_std)'
            ' VALUES (?,?,?,?,?,?,?)',
            (upd.t, upd.metric, upd.pkt_rate, upd.rssi, upd.state,
             upd.csi_dist, upd.rssi_std))
        self.con.commit()

    def recent_events(self, hours=24):
        cutoff = time.time() - hours * 3600
        rows = self.con.execute(
            'SELECT ts, state_from, state_to FROM events WHERE ts >= ? ORDER BY ts DESC LIMIT 500',
            (cutoff,)).fetchall()
        return [{'ts': r[0], 'from': r[1], 'to': r[2]} for r in rows]

    def recent_samples(self, minutes=5):
        cutoff = time.time() - minutes * 60
        rows = self.con.execute(
            'SELECT ts, metric FROM samples WHERE ts >= ? AND metric IS NOT NULL ORDER BY ts',
            (cutoff,)).fetchall()
        return [{'ts': r[0], 'metric': r[1]} for r in rows]


def run_session(packets, cfg=None):
    """Ruleaza detectorul peste un iterabil de Packet; intoarce lista de Update."""
    det = MotionDetector(cfg)
    updates = []
    for pkt in packets:
        if pkt is None:
            continue
        result = det.feed(pkt)
        if result is not None:
            updates.append(result[0])
    return updates
