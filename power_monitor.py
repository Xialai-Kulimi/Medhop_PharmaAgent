"""Read-only Linux power telemetry; never sums overlapping CPU/SoC/GPU domains."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


def discover():
    sensors = []
    seen = set()
    for root in [Path('/sys/devices/virtual/powercap'), Path('/sys/class/powercap')]:
        if not root.exists():
            continue
        for p in root.rglob('energy_uj'):
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            parent = p.parent
            try:
                sensors.append({'path': key, 'kind': 'energy',
                    'name': (parent/'name').read_text().strip(),
                    'range_uj': float((parent/'max_energy_range_uj').read_text()),
                    'readable': float(p.read_text()) >= 0,
                    'scope': 'kernel powercap zone；僅此domain，不等於整機功耗'})
            except (OSError, ValueError) as exc:
                sensors.append({'path': key, 'kind': 'energy', 'readable': False, 'error': str(exc)})
    root = Path('/sys/class/hwmon')
    for p in sorted([*root.glob('hwmon*/power*_input'), *root.glob('hwmon*/power*_average')]):
        try:
            driver = (p.parent/'name').read_text().strip()
            labelpath = p.parent/(p.name.split('_')[0]+'_label')
            label = labelpath.read_text().strip() if labelpath.exists() else p.name
            sensors.append({'path': str(p.resolve()), 'kind': 'power', 'name': driver+'/'+label+'/'+p.name,
                'readable': float(p.read_text()) >= 0,
                'scope': 'hwmon driver='+driver+'；input/average及其他domain可能重疊，分開報告'})
        except (OSError, ValueError) as exc:
            sensors.append({'path': str(p), 'kind': 'power', 'readable': False, 'error': str(exc)})
    return sensors


class PowerMonitor:
    def __init__(self, out, interval=.25):
        self.out = Path(out)
        self.interval = interval
        self.sensors = discover()
        self.rows = []
        self.previous = {}
        self.errors = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def sample(self):
        for s in self.sensors:
            if not s.get('readable'):
                continue
            try:
                value = float(Path(s['path']).read_text())
                if not math.isfinite(value) or value < 0:
                    raise ValueError('invalid sensor value')
                now = time.perf_counter()
                old = self.previous.get(s['path'])
                self.previous[s['path']] = (now, value)
                w = value/1e6 if s['kind'] == 'power' else None
                seconds = energy = None
                if old and now > old[0]:
                    seconds = now-old[0]
                    if s['kind'] == 'energy':
                        delta = value-old[1]
                        if delta < 0:
                            delta += s['range_uj']
                        if delta < 0:
                            raise ValueError('invalid energy counter wrap')
                        energy = delta/1e6
                        w = energy/seconds
                    else:
                        energy = (value+old[1])/2e6*seconds
                self.rows.append({'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                    'sensor': s['path'], 'name': s.get('name'), 'power_w': w,
                    'interval_seconds': seconds, 'interval_energy_j': energy,
                    'raw_value': value, 'kind': s['kind']})
            except (OSError, ValueError) as exc:
                self.previous.pop(s['path'], None)
                self.errors.append({'sensor': s['path'], 'error': str(exc)})

    def loop(self):
        while not self.stop.wait(self.interval):
            self.sample()

    def __enter__(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.started = datetime.now(timezone.utc).isoformat()
        self.sample()
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set(); self.thread.join(); self.sample()
        summaries = []
        for s in self.sensors:
            rows = [r for r in self.rows if r['sensor'] == s['path']]
            intervals = [r for r in rows if r['interval_energy_j'] is not None]
            duration = sum(r['interval_seconds'] for r in intervals)
            watts = [r['power_w'] for r in rows if r['power_w'] is not None]
            summaries.append({**s, 'valid_samples': len(rows), 'covered_seconds': duration,
                'mean_w': sum(r['interval_energy_j'] for r in intervals)/duration if duration else None,
                'sampled_peak_w': max(watts) if watts else None,
                'energy_j': sum(r['interval_energy_j'] for r in intervals) if intervals else None,
                'max_sample_gap_seconds': max((r['interval_seconds'] for r in intervals), default=None)})
        self.summary = {'started_utc': self.started, 'ended_utc': datetime.now(timezone.utc).isoformat(),
            'interval_seconds_requested': self.interval, 'sensors': summaries, 'errors': self.errors,
            'whole_machine_power_w': None, 'npu_power_w': None,
            'limits': ['未讀到感測器時不估算功耗。零值保留為感測器讀值，不宣稱設備無耗電。',
                       '各domain不能相加；共享背景負載包含在讀數內，不能歸因單一任務。',
                       'energy counter假設兩次讀值間最多回繞一次；大間距需人工核對。']}
        (self.out/'power_summary.json').write_text(json.dumps(self.summary, ensure_ascii=False, indent=2), encoding='utf-8')
        fields = ['timestamp_utc','sensor','name','power_w','interval_seconds','interval_energy_j','raw_value','kind']
        with (self.out/'power_samples.csv').open('w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(self.rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe', action='store_true')
    p.add_argument('--seconds', type=float, default=10)
    p.add_argument('--interval', type=float, default=.25)
    p.add_argument('--command', nargs=argparse.REMAINDER, help='量測期間執行自己的程式；不使用shell')
    args = p.parse_args()
    if args.probe:
        print(json.dumps(discover(), ensure_ascii=False, indent=2)); return
    if args.seconds <= 0 or args.interval < .05:
        p.error('seconds須大於0，interval至少0.05秒。')
    out = Path('runtime')/'hardware-benchmarks'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    child = None; code = 0
    with PowerMonitor(out, args.interval):
        if args.command:
            child = subprocess.Popen(args.command)
            try:
                code = child.wait()
            except KeyboardInterrupt:
                if child.poll() is None:
                    child.terminate()
                child.wait()
                raise
        else:
            time.sleep(args.seconds)
    print('功耗紀錄：'+str(out.resolve()))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
