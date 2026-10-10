"""Dashboard local: citeste serial, ruleaza detectorul, serveste o pagina web.

    python server.py --port COM5                 # live, un singur receptor
    python server.py --port COM5 COM6             # live, doua receptoare (max scoruri)
    python server.py --replay data/miscare_*.csv # fara placi, dintr-o inregistrare

Totul ruleaza pe laptop: fara internet, fara cont, fara cloud. Deschide
http://localhost:8000
"""

import argparse
import glob
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import csi_capture
import detector

ROOT = Path(__file__).parent

_clients = set()
_clients_lock = threading.Lock()
_latest = {'state': detector.CALIBRARE, 'metric': None, 'pkt_rate': 0.0,
           'rssi': None, 'ts': 0.0, 'threshold': None, 'sensors': {}}
_det_lock = threading.Lock()


def broadcast(payload):
    with _clients_lock:
        for q in list(_clients):
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass  # client lent; sare peste actualizare, nu blocam detectorul


def replay_source(pattern):
    """Reda o inregistrare in timp real, mutand timpii in prezent ca sa apara
    corect in istoricul de 24h."""
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise SystemExit(f'Nicio inregistrare care sa corespunda: {pattern}')
    path = matches[-1]
    print(f'Redau {path}')
    offset = None
    t_wall_start = time.time()
    for pkt in csi_capture.iter_file(path):
        if offset is None:
            offset = t_wall_start - pkt.t
        target = pkt.t + offset
        delay = target - time.time()
        if delay > 0:
            time.sleep(delay)
        yield pkt._replace(t=target)
    print('Inregistrarea s-a terminat.')


def reader_thread(source, sensor_id, det, store):
    prev = det.state
    for pkt in source:
        if pkt is None:
            continue  # inactivitate pe port; offline_thread se ocupa de ea
        with _det_lock:
            result = det.feed(sensor_id, pkt)
        if result is None:
            continue
        upd, changed, per_sensor = result
        _publish(upd, changed, prev, per_sensor, store)
        prev = upd.state


def offline_thread(det, store):
    """Detectorul nu primeste pachete cand senzorul cade, deci nu are cum sa-si
    dea seama singur. Il impingem periodic."""
    prev = det.state
    while True:
        time.sleep(1.0)
        with _det_lock:
            result = det.tick(time.time())
        if result is None:
            prev = det.state
            continue
        upd, changed, per_sensor = result
        _publish(upd, changed, prev, per_sensor, store)
        prev = upd.state


def _publish(upd, changed, prev, per_sensor, store):
    _latest.update(state=upd.state, metric=upd.metric, pkt_rate=upd.pkt_rate,
                   rssi=upd.rssi, ts=upd.t, sensors=per_sensor)
    store.sample(upd)
    if changed:
        store.event(upd.t, prev, upd.state)
        offline = [sid for sid, s in per_sensor.items() if not s['online']]
        suffix = f'  (offline: {", ".join(offline)})' if offline else ''
        print(f'{time.strftime("%H:%M:%S")}  {prev} -> {upd.state}'
              f'  (metrica {upd.metric if upd.metric is not None else float("nan"):.4f})'
              f'{suffix}')
    broadcast(dict(_latest))


class Handler(BaseHTTPRequestHandler):
    store = None

    def log_message(self, *_args):
        pass  # log-ul HTTP implicit ineaca mesajele de stare

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/':
            html = (ROOT / 'dashboard.html').read_bytes()
            return self._send(200, html, 'text/html; charset=utf-8')
        if path == '/api/state':
            return self._send(200, json.dumps(_latest))
        if path == '/api/history':
            return self._send(200, json.dumps({
                'events': self.store.recent_events(24),
                'samples': self.store.recent_samples(5),
            }))
        if path == '/api/stream':
            return self._stream()
        self._send(404, json.dumps({'error': 'not found'}))

    def _stream(self):
        q = queue.Queue(maxsize=50)
        with _clients_lock:
            _clients.add(q)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(f'data: {json.dumps(_latest)}\n\n'.encode())
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=10)
                    msg = f'data: {json.dumps(payload)}\n\n'
                except queue.Empty:
                    msg = ': heartbeat\n\n'  # tine conexiunea vie prin proxy-uri
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _clients_lock:
                _clients.discard(q)


def main():
    ap = argparse.ArgumentParser(description='Dashboard local de monitorizare.')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--port', nargs='+',
                     help='portul (sau porturile) serial ale placilor receptoare, '
                          'ex. --port COM5 COM6 pentru diversitate spatiala')
    src.add_argument('--replay', nargs='+',
                     help='reda una sau mai multe inregistrari din data/ in loc de serial')
    ap.add_argument('--http-port', type=int, default=8000)
    ap.add_argument('--alert-after', type=float,
                    help='secunde de liniste pana la alerta (implicit 300)')
    args = ap.parse_args()

    cfg = detector.load_config()
    if args.alert_after:
        cfg.quiet_to_alert_sec = args.alert_after
    if not detector.CALIBRATION_PATH.exists():
        print('ATENTIE: nu exista calibration.json, folosesc pragurile implicite.')
        print('         Ruleaza `python analyze.py --save` dupa ce inregistrezi '
              'o sesiune `gol`.')
    print(f'Praguri  CSI: {cfg.csi_threshold:.4f} / {cfg.csi_exit:.4f}   '
          f'RSSI: {cfg.rssi_threshold:.3f} / {cfg.rssi_exit:.3f}')
    print(f'Alerta dupa {cfg.quiet_to_alert_sec:.0f}s de liniste')

    sources = args.replay if args.replay else args.port
    if len(sources) > 1:
        print(f'{len(sources)} receptoare: {", ".join(sources)} (starea combinata = maximul scorurilor)')

    store = detector.Store()
    det = detector.MultiMotionDetector(sources, cfg)
    Handler.store = store
    # Metrica raportata e scorul normalizat, deci pragul e mereu 1.0.
    _latest['threshold'] = 1.0

    for sensor_id in sources:
        source = replay_source(sensor_id) if args.replay else csi_capture.iter_serial(sensor_id)
        threading.Thread(target=reader_thread, args=(source, sensor_id, det, store),
                         daemon=True).start()
    threading.Thread(target=offline_thread, args=(det, store), daemon=True).start()

    httpd = ThreadingHTTPServer(('127.0.0.1', args.http_port), Handler)
    print(f'\nDeschide http://localhost:{args.http_port}  (Ctrl+C ca sa opresti)')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nOprit.')


if __name__ == '__main__':
    main()
