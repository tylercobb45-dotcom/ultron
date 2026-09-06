"""Debug helper: dump the drag/chute-fill history around a recovery event.

Standalone - not imported by the app. Run from src/ with a real thrust
curve; run_simulation returns {'error': ...} rather than a row list when no
curve is given, which this used to not check for.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulation import run_simulation

CURVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                     'thrust_curves', 'csv', 'Hypertek_1685CCRGL-L550.csv')

results = run_simulation(5, 0.6, 0.02, 1.225, time_step=0.05,
                         chute_height=300, chute_size=0.8, chute_cd=1.8,
                         thrust_curve_path=CURVE)
if isinstance(results, dict) and 'error' in results:
    sys.exit(f"run_simulation failed: {results['error']}")

# Collect window
window = [x for x in results if 57.3 <= x['time'] <= 58.2]
print(f"{len(window)} records in 57.3-58.2s window")
for x in window:
    drag_raw = x.get('drag_raw')
    Cd_eff = x.get('Cd_eff')
    A_eff = x.get('A_eff')
    fill = x.get('chute_fill')
    print("t={:.2f} drag={:.2f} raw={} v={:.2f} alt={:.2f} Cd={} A={} fill={} limited={}".format(
        x['time'], x['drag'],
        f"{drag_raw:.2f}" if isinstance(drag_raw,(int,float)) else 'NA',
        x['velocity'], x['altitude'],
        f"{Cd_eff:.3f}" if isinstance(Cd_eff,(int,float)) else 'NA',
        f"{A_eff:.4f}" if isinstance(A_eff,(int,float)) else 'NA',
        f"{fill:.3f}" if isinstance(fill,(int,float)) else 'NA',
        x.get('drag_limit_applied')
    ))

print('Check for any sudden ratio jumps:')
for i in range(1, len(window)):
    prev = window[i-1]
    cur = window[i]
    if prev['drag'] > 0:
        ratio = cur['drag']/prev['drag']
        if ratio > 1.5:
            print(f"Jump at {cur['time']:.2f}s ratio {ratio:.2f}")
