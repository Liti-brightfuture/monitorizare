"""Detectie de respiratie: e cineva in pat, chiar daca nu se misca?

Detectorul de miscare din detector.py nu poate raspunde la asta. Masurat pe o
noapte reala, mediana semnalului cu un om adormit (0.0002) e identica cu cea a
camerei goale, iar pauzele fara nicio miscare detectata ajung la 68 de minute --
deci o alerta de inactivitate ar cere un prag inutilizabil de 90 de minute.

Respiratia umple exact golul asta. Pieptul care se ridica si coboara moduleaza
toate subpurtatoarele la ACEEASI frecventa, in timp ce varfurile de zgomot sunt
independente de la o subpurtatoare la alta.

De aceea criteriul NU e puterea din banda respiratiei -- masurat, camera goala
are chiar putere mai mare acolo -- ci ACORDUL intre subpurtatoare asupra
frecventei:

    somn         varf 13.1-13.9 resp/min, abatere 0.00-0.91
    camera goala                          abatere 4.17-7.29

Folosim blocul HT-LTF (indicii 64+), spre deosebire de detector.py care
foloseste LLTF pentru miscare. In toate ferestrele de somn analizate, 9-10 din
primele 10 subpurtatoare purtatoare de semnal erau in HT-LTF: acopera 40 MHz in
loc de 20, deci ofera mai multe subpurtatoare independente care sa confirme
aceeasi frecventa.
"""

from collections import deque, namedtuple

import numpy as np

import csi_capture

RESAMPLE_HZ = 20.0          # mult peste Nyquist pentru 0.5 Hz
BAND = (0.16, 0.50)         # banda respiratiei: 9.6 - 30 resp/min
REF_BAND = (0.05, 2.0)      # fata de ce comparam puterea
TOP_N = 10                  # peste cate subpurtatoare luam mediana varfului
MIN_RATE, MAX_RATE = 8.0, 30.0

# Confirmarea cere ca ultimele K ferestre sa dea aceeasi rata, in limita tol.
#
# Acordul in INTERIORUL unei ferestre nu e o dovada: subpurtatoarele adiacente
# sunt corelate (fizic, plus firmware-ul le netezeste cu channel_filter_en),
# deci un singur varf de zgomot apare in multe deodata. Masurat pe camera
# goala, ferestre intregi au dat acord perfect -- dar la rate complet diferite.
#
# Nici trei ferestre la rand nu ajung. Daca varfurile ar fi plasate aleator in
# 8-30 resp/min, sansa ca trei consecutive sa cada in +/-3 e ~5%; masurat pe un
# control real de doua ore, exact 9.3%. Adica fals-pozitivele erau simple
# coincidente pe care regula le permitea prin constructie.
#
# Masurat pe 118 ferestre de camera cu adevarat goala:
#   3 ferestre in +/-3 -> 11 fals-pozitive (9.3%)
#   4 ferestre in +/-3 ->  2 fals-pozitive
#   5 ferestre in +/-3 ->  0 fals-pozitive, 18.2% acoperire
# Alegem 5. Costa acoperire, dar un sistem care raporteaza "respira" intr-un pat
# gol e mai periculos decat unul care tace.
K_WINDOWS = 5
RATE_TOL = 3.0
FADE_DB = 8.0               # sub cat dB fata de mediana aruncam un pachet
MIN_PACKETS = 200

Breath = namedtuple('Breath', 't rate spread bnr n_sub confirmed')


def select_subcarriers(amps):
    """Indicii utili: blocul HT-LTF, fara subpurtatoarele nule si fara prima.

    Subpurtatoarele nule si de garda sunt exact zero in firmware, iar primul
    indice e marcat `first_word_invalid`.
    """
    keep = (amps > 0).mean(axis=0) > 0.9
    keep[0] = False
    if amps.shape[1] > 64:
        keep[:64] = False
    return np.where(keep)[0]


def analyze_window(times, amps, rssis, fade_db=FADE_DB):
    """O fereastra de pachete -> Breath, sau None daca nu e utilizabila.

    Intoarce mereu `confirmed=False`: confirmarea cere istoric, si o pune
    iter_windows. Aici masuram doar rata acestei ferestre.
    """
    times = np.asarray(times, dtype=float)
    amps = np.asarray(amps, dtype=float)
    rssis = np.asarray(rssis, dtype=float)
    if len(times) < MIN_PACKETS:
        return None

    # Cam 5% din pachete sosesc cu 15-20 dB sub restul. Sunt impulsuri, si un
    # impuls imprastie energie pe toate frecventele, inecand banda respiratiei.
    keep = rssis >= np.median(rssis) - fade_db
    times, amps = times[keep], amps[keep]
    if len(times) < MIN_PACKETS:
        return None

    cols = select_subcarriers(amps)
    if len(cols) < TOP_N:
        return None

    grid = np.arange(times[0], times[-1], 1.0 / RESAMPLE_HZ)
    if len(grid) < 30 * RESAMPLE_HZ:      # sub 30s nu se poate masura 0.2 Hz
        return None
    sig = np.stack([np.interp(grid, times, amps[:, c]) for c in cols], axis=1)

    # Detrend patratic: scoate deriva lenta a canalului (mobila mutata, drift
    # termic) fara sa atinga banda respiratiei. Apoi fereastra Hann, ca sa nu
    # imprastie scurgerea spectrala energie in banda.
    x = np.arange(len(grid))
    basis = np.vander(x, 3)
    coef, *_ = np.linalg.lstsq(basis, sig, rcond=None)
    sig -= basis @ coef
    sig *= np.hanning(len(grid))[:, None]

    freqs = np.fft.rfftfreq(len(grid), 1.0 / RESAMPLE_HZ)
    power = np.abs(np.fft.rfft(sig, axis=0)) ** 2
    in_band = (freqs >= BAND[0]) & (freqs <= BAND[1])
    in_ref = (freqs >= REF_BAND[0]) & (freqs <= REF_BAND[1]) & (~in_band)
    if in_band.sum() < 3 or in_ref.sum() < 3:
        return None

    # Putere MEDIE per bin in fiecare zona, ca raportul sa fie 1.0 la zgomot alb
    # indiferent cat de late sunt benzile.
    bnr = ((power[in_band].sum(axis=0) / in_band.sum())
           / np.maximum(power[in_ref].sum(axis=0) / in_ref.sum(), 1e-12))
    peaks = freqs[in_band][np.argmax(power[in_band], axis=0)] * 60.0

    best = np.argsort(bnr)[::-1][:TOP_N]
    return Breath(float(times[0]), float(np.median(peaks[best])),
                  float(np.std(peaks[best])), float(np.median(bnr[best])),
                  len(cols), False)


def confirms(rates):
    """Ultimele K rate cad de acord si sunt fiziologic plauzibile?"""
    if len(rates) < K_WINDOWS:
        return False
    lo, hi = min(rates), max(rates)
    return bool(hi - lo <= RATE_TOL and MIN_RATE <= lo and hi <= MAX_RATE)


def iter_windows(path, window_sec=120.0, step_sec=60.0, fade_db=FADE_DB):
    """Ferestre glisante peste o inregistrare.

    Tine in memorie o singura fereastra, ca sa mearga si pe fisiere de gigabytes.
    """
    bt, ba, br = deque(), deque(), deque()
    recent = deque(maxlen=K_WINDOWS)
    next_emit = None
    for pkt in csi_capture.iter_file(path):
        if pkt is None:
            continue
        bt.append(pkt.t)
        ba.append(pkt.amp)
        br.append(pkt.rssi)
        while bt and pkt.t - bt[0] > window_sec:
            bt.popleft()
            ba.popleft()
            br.popleft()
        if next_emit is None:
            next_emit = pkt.t + window_sec
        if pkt.t >= next_emit:
            next_emit += step_sec
            if len(ba) >= MIN_PACKETS:
                res = analyze_window(list(bt), np.stack(ba), list(br), fade_db)
                if res is not None:
                    recent.append(res.rate)
                    yield res._replace(confirmed=confirms(recent))
