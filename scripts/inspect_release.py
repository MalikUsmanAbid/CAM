"""Read-only package integrity inspection; never starts installed/portable apps."""
import argparse
import json
from pathlib import Path

from scripts.release_integrity import inspect_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    value = parser.parse_args()
    print(json.dumps(inspect_release(value.directory), indent=2))


if __name__ == '__main__':
    main()
