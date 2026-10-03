"""Resolve and download the latest Fedora CoreOS stable x86_64 Live DVD for CI."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import urlopen

STREAM = "https://builds.coreos.fedoraproject.org/streams/stable.json"


def validate_selection(selection):
    url = urlparse(selection["location"])
    if url.scheme != "https" or url.netloc != "builds.coreos.fedoraproject.org":
        raise ValueError("Unexpected Fedora ISO origin")
    if not re.fullmatch(r"[a-f0-9]{64}", selection["sha256"]):
        raise ValueError("Invalid Fedora ISO checksum")
    if not re.fullmatch(r"[0-9.]+", selection["release"]):
        raise ValueError("Invalid Fedora release identifier")


def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with urlopen(STREAM, timeout=60) as response:
        stream = json.load(response)
    artifact = stream["architectures"]["x86_64"]["artifacts"]["metal"]
    selection = dict(artifact["formats"]["iso"]["disk"], release=artifact["release"])
    validate_selection(selection)
    (directory / "selection.json").write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    return selection


def digest(path):
    with Path(path).open("rb") as fp:
        return hashlib.file_digest(fp, "sha256").hexdigest()


def fetch(directory):
    directory = Path(directory)
    selection = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    validate_selection(selection)
    output = directory / "base.iso"
    if output.exists():
        if digest(output) == selection["sha256"]:
            return output
        output.unlink()  # Reject a corrupt/stale cache before downloading again.
    try:
        with urlopen(selection["location"], timeout=120) as response, output.open("xb") as fp:
            while chunk := response.read(4 * 1024 * 1024):
                fp.write(chunk)
        if digest(output) != selection["sha256"]:
            raise ValueError("Downloaded Fedora ISO SHA-256 does not match stable stream metadata")
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "fetch"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.operation == "prepare":
        selection = prepare(args.directory)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as fp:
                fp.write("release=" + selection["release"] + "\nsha256=" + selection["sha256"] + "\n")
        print("Selected Fedora CoreOS stable " + selection["release"])
    else:
        print("Verified " + str(fetch(args.directory)))


if __name__ == "__main__":
    main()
