from __future__ import annotations

import argparse
import subprocess


def validate_staged_paths(paths: list[str]) -> None:
    invalid = [path for path in paths if not path.replace("\\", "/").startswith("data/research/")]
    if invalid:
        raise ValueError("staged paths outside data/research/: " + ", ".join(invalid))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate daily research commit scope")
    parser.add_argument("--validate-staged", action="store_true", required=True)
    parser.parse_args(argv)
    output = subprocess.run(["git", "diff", "--cached", "--name-only"], check=True,
                            capture_output=True, text=True).stdout
    validate_staged_paths([line for line in output.splitlines() if line])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
