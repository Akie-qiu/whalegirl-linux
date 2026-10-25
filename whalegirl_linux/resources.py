"""Read the watchdog's small numeric projection; no additional UI probes."""
import json
import time


def native_pressure(config):
    try:
        sample = json.loads((config.directory/'stability/native-resources.json').read_text())
        return bool(0 <= time.time()-sample.get('at', 0) < 180 and sample.get('pressure'))
    except (OSError, ValueError, TypeError):
        return False
