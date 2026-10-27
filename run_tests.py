"""Run all offline regression scripts in separate processes."""
from pathlib import Path
import subprocess
import sys


def main():
    failures = []
    root = Path(__file__).resolve().parent
    for script in sorted(root.glob('test_*.py')):
        result = subprocess.run([sys.executable, str(script)], cwd=root)
        if result.returncode:
            failures.append(script.name)
    if failures:
        sys.exit('FAILED: ' + ', '.join(failures))
    print('All offline regression scripts passed.')


if __name__ == '__main__':
    main()
