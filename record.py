"""Inregistreaza o sesiune CSI etichetata, pentru masurarea acuratetii.

Fara sesiuni etichetate, "merge" e doar o impresie. Astea sunt datele pe care
analyze.py calculeaza criteriile de succes.

Sesiunile de inregistrat (vezi README):
    python record.py --port COM5 --label gol            --seconds 1800
    python record.py --port COM5 --label nemiscat       --seconds 300
    python record.py --port COM5 --label miscare        --seconds 300
    python record.py --port COM5 --label intrari_iesiri --seconds 300 --cues 30

Cu --cues N, scriptul da un semnal sonor si scrie pe ecran alternativ
"IESI ACUM" / "INTRA ACUM" la fiecare N secunde, si salveaza momentele exacte
intr-un fisier .marks.csv. Asta e adevarul de referinta pentru latenta -- nu
ai cum sa apesi o tasta in timp ce iesi din camera.
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import csi_capture

DATA_DIR = Path(__file__).parent / 'data'


def beep(count):
    """Semnal sonor pe Windows; pe alte sisteme, caracterul BEL."""
    try:
        import winsound
        for _ in range(count):
            winsound.Beep(880, 200)
            time.sleep(0.1)
    except Exception:
        sys.stdout.write('\a' * count)
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description='Inregistreaza o sesiune CSI etichetata.')
    ap.add_argument('--port', required=True, help='portul serial al placii receptoare, ex. COM5')
    ap.add_argument('--label', required=True, help='eticheta sesiunii: gol / nemiscat / miscare / intrari_iesiri')
    ap.add_argument('--seconds', type=float, default=300, help='durata inregistrarii (implicit 300)')
    ap.add_argument('--cues', type=float, default=0,
                    help='da o comanda sonora IESI/INTRA la fiecare N secunde (0 = fara)')
    ap.add_argument('--out-dir', default=str(DATA_DIR),
                    help='unde salvam (implicit data/). Foloseste un subdirector '
                         'pentru un set separat de masuratori, ca sa nu umbresti '
                         'sesiunile existente')
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    out_path = out_dir / f'{args.label}_{stamp}.csv'
    marks_path = out_dir / f'{args.label}_{stamp}.marks.csv'

    print(f'Inregistrez in {out_path.name} timp de {args.seconds:.0f}s. Ctrl+C ca sa opresti.')
    if args.cues:
        print(f'Comenzi sonore la fiecare {args.cues:.0f}s. Urmeaza-le exact.')
        print('Pozitioneaza-te IN camera la start.')

    marks_fd = None
    marks_writer = None
    if args.cues:
        marks_fd = open(marks_path, 'w', newline='', encoding='utf-8')
        marks_writer = csv.writer(marks_fd)
        marks_writer.writerow(['host_time', 'cue'])

    t_start = time.time()
    next_cue = t_start + args.cues if args.cues else None
    cue_is_exit = True  # prima comanda: iesi din camera
    n_ok = 0
    n_rejected = 0
    last_report = t_start

    def on_reject(_line):
        nonlocal n_rejected
        n_rejected += 1

    try:
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            header_written = False
            for pkt in csi_capture.iter_serial(args.port, on_reject=on_reject):
                # None = nu vine nimic pe port. Verificam oricum ceasul, ca sa
                # nu ramanem blocati daca emitatorul e oprit sau prea departe.
                if pkt is None:
                    now = time.time()
                    if n_ok == 0 and now - t_start >= 10 and now - last_report >= 5:
                        print(f'  {now - t_start:6.0f}s  ZERO pachete. Verifica: '
                              'emitatorul e alimentat? e prea departe? '
                              'ai portul corect?')
                        last_report = now
                    if now - t_start >= args.seconds:
                        break
                    continue

                now = pkt.t
                if not header_written:
                    names = csi_capture.columns_for(len(pkt.fields))
                    writer.writerow(['host_time'] + names)
                    header_written = True
                writer.writerow([f'{now:.6f}'] + pkt.fields)
                n_ok += 1

                if next_cue is not None and now >= next_cue:
                    cue = 'IESI' if cue_is_exit else 'INTRA'
                    marks_writer.writerow([f'{now:.6f}', cue])
                    marks_fd.flush()
                    print(f'\n>>> {cue} ACUM <<<')
                    beep(1 if cue_is_exit else 2)
                    cue_is_exit = not cue_is_exit
                    next_cue += args.cues

                if now - last_report >= 5:
                    elapsed = now - t_start
                    rate = n_ok / elapsed if elapsed else 0
                    print(f'  {elapsed:6.0f}s  {n_ok:7d} pachete  {rate:6.1f} pachete/s  '
                          f'{n_rejected} respinse  RSSI {pkt.rssi} dBm  '
                          f'{len(pkt.amp)} subpurtatoare')
                    last_report = now

                if now - t_start >= args.seconds:
                    break
    except KeyboardInterrupt:
        print('\nOprit manual.')
    finally:
        if marks_fd:
            marks_fd.close()

    elapsed = time.time() - t_start
    print(f'\nGata: {n_ok} pachete in {elapsed:.0f}s ({n_ok / elapsed if elapsed else 0:.1f}/s), '
          f'{n_rejected} linii respinse.')
    print(f'Salvat: {out_path}')
    if n_ok == 0:
        print('ATENTIE: zero pachete valide. Verifica portul si daca placa emitatoare e pornita.')


if __name__ == '__main__':
    main()
