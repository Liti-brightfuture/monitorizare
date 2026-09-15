"""Citire si parsare a datelor CSI de la ESP32.

Sursa de date este exemplul `csi_recv` din espressif/esp-csi: placa receptoare
scoate pe serial (921600 baud) cate o linie CSV per pachet receptionat, in care
ultima coloana este un array JSON `[imag1, real1, imag2, real2, ...]`.

Doua layout-uri de coloane exista in firmware:
  - ESP32 / S2 / C3 / S3  -> 25 de coloane (COLS_CLASSIC)
  - ESP32-C5 / C6         -> 15 coloane   (COLS_C5C6)
Detectam automat care dintre ele, dupa numarul de campuri.
"""

import csv
import json
import time
from collections import namedtuple
from io import StringIO

import numpy as np

BAUDRATE = 921600

COLS_CLASSIC = ['type', 'id', 'mac', 'rssi', 'rate', 'sig_mode', 'mcs', 'bandwidth',
                'smoothing', 'not_sounding', 'aggregation', 'stbc', 'fec_coding', 'sgi',
                'noise_floor', 'ampdu_cnt', 'channel', 'secondary_channel',
                'local_timestamp', 'ant', 'sig_len', 'rx_state', 'len', 'first_word', 'data']

COLS_C5C6 = ['type', 'id', 'mac', 'rssi', 'rate', 'noise_floor', 'fft_gain', 'agc_gain',
             'channel', 'local_timestamp', 'sig_len', 'rx_state', 'len', 'first_word', 'data']

# t = timpul gazdei (secunde, time.time()); amp = amplitudine per subpurtatoare
Packet = namedtuple('Packet', 't rssi amp fields')


def columns_for(n_fields):
    if n_fields == len(COLS_CLASSIC):
        return COLS_CLASSIC
    if n_fields == len(COLS_C5C6):
        return COLS_C5C6
    return None


def parse_fields(fields):
    """Campuri CSV deja separate -> (meta, amplitudini) sau None daca linia e invalida."""
    names = columns_for(len(fields))
    if names is None:
        return None
    meta = dict(zip(names, fields))
    try:
        raw = json.loads(meta['data'])
    except (json.JSONDecodeError, TypeError):
        return None
    # Linie trunchiata de bufferul serial: lungimea reala nu corespunde campului `len`.
    if len(raw) != int(meta['len']) or len(raw) % 2:
        return None
    a = np.asarray(raw, dtype=np.float32)
    # Partea imaginara e stocata prima, apoi cea reala.
    amp = np.hypot(a[1::2], a[0::2])
    return meta, amp


def parse_line(line):
    """O linie bruta de pe serial -> (meta, amplitudini, campuri) sau None."""
    i = line.find('CSI_DATA')
    if i == -1:
        return None
    try:
        fields = next(csv.reader(StringIO(line[i:])))
    except (csv.Error, StopIteration):
        return None
    parsed = parse_fields(fields)
    if parsed is None:
        return None
    meta, amp = parsed
    return meta, amp, fields


def iter_serial(port, baudrate=BAUDRATE, on_reject=None):
    """Genereaza Packet-uri de pe portul serial, la nesfarsit.

    Cand nu vine niciun pachet valid, genereaza `None` cam o data pe secunda.
    Fara semnalul asta, un apelant nu are cum sa distinga "totul e linistit" de
    "emitatorul e oprit si asteptam degeaba" -- si ramane blocat la nesfarsit.

    on_reject(motiv) e apelat pentru fiecare linie respinsa, ca sa poti vedea
    daca pierzi multe pachete (semn de baud rate sau buffer prea mic).
    """
    import serial  # importat aici ca analyze.py sa mearga fara pyserial instalat

    with serial.Serial(port=port, baudrate=baudrate, bytesize=8,
                       parity='N', stopbits=1, timeout=1) as ser:
        last_yield = time.time()
        while True:
            line = ser.readline().decode('utf-8', errors='ignore')
            parsed = parse_line(line) if 'CSI_DATA' in line else None
            if parsed is None:
                if line and 'CSI_DATA' in line and on_reject:
                    on_reject(line.rstrip())
                now = time.time()
                if now - last_yield >= 1.0:
                    last_yield = now
                    yield None
                continue
            meta, amp, fields = parsed
            last_yield = time.time()
            yield Packet(last_yield, int(meta['rssi']), amp, fields)


def iter_file(path):
    """Reda o sesiune inregistrata de record.py, cu timpii originali.

    Redarea e instantanee: detectorul foloseste exclusiv Packet.t, niciodata
    ceasul de perete, deci rezultatul e identic cu rularea live.
    """
    with open(path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header or header[0] != 'host_time':
            raise ValueError(f'{path}: nu are header-ul asteptat (host_time,...)')
        for row in reader:
            if len(row) < 2:
                continue
            parsed = parse_fields(row[1:])
            if parsed is None:
                continue
            meta, amp = parsed
            yield Packet(float(row[0]), int(meta['rssi']), amp, row[1:])
