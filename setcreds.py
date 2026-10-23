#!/usr/bin/env python3
"""Write Meta credentials into .env without echoing them.

The value never appears on screen, in shell history, or in a chat transcript.

    python3 setcreds.py IG_ACCESS_TOKEN              # prompt, hidden input
    python3 setcreds.py IG_ACCESS_TOKEN --clipboard  # take it from the clipboard

--clipboard is the safer route for long tokens. A hidden prompt gives no visual
confirmation that the paste landed whole, and a silently truncated token fails
later with a confusing auth error rather than at the point of the mistake.
"""
import getpass
import subprocess
import pathlib
import sys

ALLOWED = {"IG_ACCESS_TOKEN", "META_APP_SECRET", "IG_USER_ID", "META_APP_ID",
           "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID",
           "R2_BUCKET", "R2_PUBLIC_URL"}
ENV = pathlib.Path(__file__).parent / ".env"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    if len(args) != 1 or args[0] not in ALLOWED:
        sys.exit(f"usage: setcreds.py {{{'|'.join(sorted(ALLOWED))}}} [--clipboard]")
    key = args[0]

    if "--clipboard" in flags:
        try:
            value = subprocess.run(["pbpaste"], capture_output=True, text=True,
                                   check=True).stdout.strip()
        except Exception as e:
            sys.exit(f"could not read the clipboard: {e}")
        if not value:
            sys.exit("clipboard is empty, .env unchanged")
    else:
        value = getpass.getpass(f"Paste {key} (input hidden, then Enter): ").strip()
    if not value:
        sys.exit("nothing entered, .env unchanged")
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        value = value[1:-1].strip()

    lines = ENV.read_text().splitlines() if ENV.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")

    ENV.write_text("\n".join(out) + "\n")
    ENV.chmod(0o600)
    print(f"{key} written to .env ({len(value)} chars). Value not displayed.")


if __name__ == "__main__":
    main()
