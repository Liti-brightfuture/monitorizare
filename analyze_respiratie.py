"""Valideaza detectia de respiratie pe inregistrari deja facute.

    python analyze_respiratie.py --data-dir data/noapte

Are nevoie de doua sesiuni in acel director:
  noapte_*.csv  -- omul a fost in pat tot timpul  => adevarul e "prezent"
  gol_*.csv     -- camera a fost goala            => adevarul e "absent"

Intrebarea la care raspunde nu e "cat de des detectam respiratia" ci
"cat de lung e cel mai mare interval in care sistemul nu vede niciun semn de
viata" -- de acolo iese pragul de alerta care se poate promite unui camin.
"""

import argparse
import sys
import time
from dataclasses import replace

import numpy as np

import analyze
import breathing
import detector

ALERT_TARGET_MIN = 30.0    # pragul de alerta pe care vrem sa-l putem promite


def gaps(t0, t1, events):
    """Intervalele fara niciun semn de viata, si momentul in care s-au incheiat."""
    bounds = np.concatenate(([t0], np.sort(events), [t1]))
    return np.diff(bounds), bounds[1:]


def motion_events(sessions, data_dir):
    """Momentele in care detectorul de miscare a vazut ceva, peste noapte."""
    nt, nc, nr, _ = analyze.metric_series(sessions['noapte'], detector.Config())
    gt, gc, gr, _ = analyze.metric_series(sessions['gol'], detector.Config())
    keep = gt >= gt[0] + 120
    if keep.sum() > 100:
        gt, gc, gr = gt[keep], gc[keep], gr[keep]

    cfg = None
    for k in (2, 3, 4, 6, 8, 10, 13, 16, 20, 25, 30, 40, 50, 65, 80, 100):
        ct, ce, _, _ = analyze.calibrate(gc, k, 1e-4)
        rt, re_, _, _ = analyze.calibrate(gr, k, 0.05)
        c = replace(detector.Config(), csi_threshold=ct, csi_exit=ce,
                    rssi_threshold=rt, rssi_exit=re_)
        if not any(s == detector.MISCARE
                   for _, s in analyze.replay_states(gt, gc, gr, c)):
            cfg = c
            print(f'  praguri de miscare din camera goala, k={k}')
            break
    if cfg is None:
        # Camera goala nu a fost destul de linistita ca sa curete niciun prag.
        # Calibram din noaptea insasi: perioadele de somn nemiscat sunt ele
        # linia de baza. Mai putin riguros, dar singura optiune cu datele astea.
        cm, rm = float(np.median(nc)), float(np.median(nr))
        cd = max(float(np.median(np.abs(nc - cm))), 1e-4)
        rd = max(float(np.median(np.abs(nr - rm))), 0.05)
        cfg = replace(detector.Config(), csi_threshold=cm + 8 * cd,
                      csi_exit=cm + 4 * cd, rssi_threshold=rm + 8 * rd,
                      rssi_exit=rm + 4 * rd)
        print('  ATENTIE: camera goala e prea zgomotoasa pentru calibrare;')
        print('           praguri de miscare luate din noaptea insasi (k=8)')

    st = analyze.states_over(nt, nc, nr, cfg)
    return nt[0], nt[-1], nt[st == detector.MISCARE]


def fmt(ok):
    return 'PASS' if ok else 'FAIL'


def main():
    ap = argparse.ArgumentParser(description='Validare detectie de respiratie.')
    ap.add_argument('--data-dir', default='data/noapte')
    args = ap.parse_args()

    sessions = analyze.find_sessions(args.data_dir)
    # `golcurat` = control inregistrat cu casa linistita; e preferat lui `gol`,
    # care de obicei prinde operatorul miscandu-se prin casa.
    for alias in ('golcurat', 'gol'):
        if alias in sessions:
            sessions['gol'] = sessions[alias]
            print(f'Control de camera goala: {sessions[alias].name}')
            break
    for need in ('noapte', 'gol'):
        if need not in sessions:
            print(f'Lipseste sesiunea `{need}` in {args.data_dir}.')
            return 1

    print('Procesez respiratia (cateva minute pentru o noapte)...')
    night = list(breathing.iter_windows(sessions['noapte']))
    empty = list(breathing.iter_windows(sessions['gol']))
    if not night or not empty:
        print('Prea putine ferestre utilizabile.')
        return 1

    nt = np.array([r.t for r in night])
    n_ok = np.array([r.confirmed for r in night])
    n_rate = np.array([r.rate for r in night])
    e_ok = np.array([r.confirmed for r in empty])

    print(f'  noapte: {len(night)} ferestre pe {(nt[-1]-nt[0])/3600:.1f} h, '
          f'{100*n_ok.mean():.1f}% confirma')
    print(f'  gol   : {len(empty)} ferestre, {int(e_ok.sum())} confirmari')

    print('\nProcesez miscarea...')
    t0, t1, mov_t = motion_events(sessions, args.data_dir)

    g_bre, _ = gaps(t0, t1, nt[n_ok])
    g_mov, _ = gaps(t0, t1, mov_t)
    g_all, e_all = gaps(t0, t1, np.concatenate([mov_t, nt[n_ok]]))

    print('\nCat de lung e intervalul fara niciun semn de viata:')
    print(f'  {"semnal":24s} {"p90":>9} {"MAXIMA":>9}')
    for name, g in (('doar miscare', g_mov), ('doar respiratie', g_bre),
                    ('ambele combinate', g_all)):
        print(f'  {name:24s} {np.percentile(g,90)/60:8.1f}\' {g.max()/60:8.1f}\'')

    print('\nCele mai lungi 5 pauze combinate:')
    for i in np.argsort(g_all)[-5:][::-1]:
        print(f'  {g_all[i]/60:6.1f} min, s-a incheiat la '
              f'{time.strftime("%H:%M", time.localtime(e_all[i]))}')

    print('\n' + '=' * 74)
    print('CRITERII DE SUCCES')
    print('=' * 74)
    results = []

    n_fp = int(e_ok.sum())
    ok = n_fp == 0
    results.append(ok)
    print(f'[{fmt(ok)}] Fals-pozitive      {n_fp} confirmari de respiratie in '
          f'{len(e_ok)} ferestre de camera goala   (prag 0)')

    ok = bool(g_all.max() <= ALERT_TARGET_MIN * 60)
    results.append(ok)
    print(f'[{fmt(ok)}] Prag utilizabil    cea mai lunga pauza combinata '
          f'{g_all.max()/60:.1f} min   (prag {ALERT_TARGET_MIN:.0f} min)')

    ok = bool(g_all.max() < g_mov.max())
    results.append(ok)
    print(f'[{fmt(ok)}] Aport propriu      respiratia scurteaza pauza maxima de '
          f'la {g_mov.max()/60:.1f} la {g_all.max()/60:.1f} min')

    if n_ok.any():
        good = n_rate[n_ok]
        med = float(np.median(good))
        frac = float(np.mean(np.abs(good - med) <= 2.0))
        ok = frac >= 0.80
        results.append(ok)
        print(f'[{fmt(ok)}] Stabilitatea ratei {100*frac:.1f}% din confirmari in '
              f'+/-2 resp/min fata de mediana {med:.1f}   (prag 80%)')
    else:
        results.append(False)
        print('[FAIL] Stabilitatea ratei nicio confirmare')

    print('=' * 74)
    if all(results):
        print(f'VERDICT: se poate promite o alerta de inactivitate nocturna la '
              f'{ALERT_TARGET_MIN:.0f} min.')
    else:
        print('VERDICT: NU integra in dashboard.')
        if n_fp:
            print('Fals-pozitivele sunt cel mai grav: sistemul ar raporta ca')
            print('respira cineva intr-un pat gol. Creste K_WINDOWS sau scade')
            print('RATE_TOL in breathing.py.')
    return 0 if all(results) else 2


if __name__ == '__main__':
    sys.exit(main())
