"""Apply the boot profile (tuf-control-boot.service), or with --reapply the last
applied profile (tuf-control-resume.service: after sleep / charger plug-unplug)."""
import json
import sys

from . import profiles


def main() -> int:
    results = profiles.apply_boot(reapply="--reapply" in sys.argv)
    print(json.dumps(results, indent=2) if results else "no profile to apply")
    return 0 if all(profiles.is_ok(v) for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
