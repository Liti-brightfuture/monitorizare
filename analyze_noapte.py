"""Cat de lungi sunt pauzele fara miscare la un om care doarme?

Asta e intrebarea de care depinde tot cazul de business. Sistemul detecteaza
miscare, nu prezenta: un om care doarme nemiscat si un pat gol arata identic.
Daca cineva sta nemiscat 40 de minute intre doua intoarceri in pat, iar alerta
e setata la 30, sistemul da alarma degeaba la 3 dimineata.

Scriptul masoara distributia pauzelor peste noapte si spune, pentru fiecare prag
de alerta candidat, cate alarme false ar fi iesit.

    python analyze_noapte.py --data-dir data/noapte

Are nevoie de doua sesiuni in acel director:
  gol_*.csv     -- dormitorul gol, ca sa calibreze pragurile pentru camera aia
  noapte_*.csv  -- noaptea propriu-zisa
"""

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

import analyze
import detector


def main():
    ap = argparse.ArgumentParser(description='Analiza unei nopti de somn.')
    ap.add_argument('--data-dir', default='data/noapte')
    ap.add_argument('--gol-skip', type=float, default=120.0)
    ap.add_argument('--k', default='auto')
    args = ap.parse_args()

    sessions = analyze.find_sessions(args.data_dir)
    for need in ('gol', 'noapte'):
        if need not in sessions:
            print(f'Lipseste sesiunea `{need}` in {args.data_dir}.')
            return 1

    base = detector.Config()
    print('Procesez inregistrarile (poate dura un minut pentru o noapte)...\n')
    gt, gc, gr, gp = analyze.metric_series(sessions['gol'], base)
    nt, nc, nr, npk = analyze.metric_series(sessions['noapte'], base)

    keep = gt >= gt[0] + args.gol_skip
    if keep.sum() > 100:
        gt, gc, gr = gt[keep], gc[keep], gr[keep]

    dur_h = (npk[-1] - npk[0]) / 3600
    print(f'Dormitor gol : {(gp[-1]-gp[0])/60:.0f} min, {len(gp)} pachete')
    print(f'Noapte       : {dur_h:.1f} ore, {len(npk)} pachete, '
          f'{len(npk)/(npk[-1]-npk[0]):.1f}/s')

    def build(k):
        ct, ce, _, _ = analyze.calibrate(gc, k, 1e-4)
        rt, re_, _, _ = analyze.calibrate(gr, k, 0.05)
        return replace(base, csi_threshold=ct, csi_exit=ce,
                       rssi_threshold=rt, rssi_exit=re_)

    if args.k == 'auto':
        k = 100.0
        for cand in (2, 3, 4, 5, 6, 8, 10, 13, 16, 20, 25, 30, 40, 50, 65, 80, 100):
            cfg = build(cand)
            if not any(s == detector.MISCARE
                       for _, s in analyze.replay_states(gt, gc, gr, cfg)):
                k = float(cand)
                break
        print(f'\nk ales automat din dormitorul gol: {k:.0f}')
    else:
        k = float(args.k)
    cfg = build(k)

    st = analyze.states_over(nt, nc, nr, cfg)
    moving = st == detector.MISCARE
    print(f'Din noapte, detectat ca miscare: {100*moving.mean():.1f}% din timp')

    idx = np.where(moving)[0]
    if len(idx) == 0:
        print('\nZERO miscare detectata toata noaptea. Ori pragul e prea sus,')
        print('ori placile nu vedeau patul. Nu se poate trage o concluzie.')
        return 2

    bounds = np.concatenate(([nt[0]], nt[idx], [nt[-1]]))
    all_gaps = np.diff(bounds)
    ends = bounds[1:]                # momentul in care s-a terminat fiecare pauza
    sel = all_gaps > 1.0             # ignoram intervalele dintre ferestre vecine
    gaps, ends = all_gaps[sel], ends[sel]

    print(f'\nPauze fara miscare ({len(gaps)} in total), in minute:')
    for q in (50, 75, 90, 95, 99):
        print(f'  p{q:<3d} {np.percentile(gaps, q)/60:6.1f}')
    print(f'  max  {gaps.max()/60:6.1f}')

    print('\nCele mai lungi 10 pauze:')
    print(f'  {"minute":>7}  {"s-a terminat la":>16}')
    for i in np.argsort(gaps)[-10:][::-1]:
        ora = time.strftime('%H:%M:%S', time.localtime(ends[i]))
        print(f'  {gaps[i]/60:7.1f}  {ora:>16}')

    print('\n' + '=' * 62)
    print('CATE ALARME FALSE AR FI DAT, PENTRU FIECARE PRAG DE ALERTA')
    print('=' * 62)
    print(f'{"prag":>8} {"alarme in noaptea asta":>24} {"pe noapte":>12}')
    for minutes in (10, 15, 20, 30, 45, 60, 90):
        n = int((gaps >= minutes * 60).sum())
        print(f'{minutes:6d}m {n:24d} {n/max(dur_h/8, 1e-9):11.1f}')
    print('\nAlege pragul cel mai mic cu ZERO alarme, si mai pune marja.')
    print('Un prag peste ~60 min inseamna ca produsul nu prea mai ajuta noaptea:')
    print('o cadere la 2 dimineata ar fi semnalata abia la 3.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
