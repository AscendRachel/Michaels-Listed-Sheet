"""Start the Greentek Catalog server, or run a maintenance command.

    python run.py                          start the server
    python run.py adduser rachel --name "Rachel" --admin
    python run.py resetpassword rachel
    python run.py agentkey                 print the scraper key
"""

import argparse
import getpass
import os
import socket
import sys


def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def read_pw(from_stdin):
    if from_stdin:
        return sys.stdin.readline().lstrip("﻿").rstrip("\r\n")     # PowerShell pipes add a byte-order mark and a \r
    p1 = getpass.getpass("Password (8+ characters): ")
    if p1 != getpass.getpass("Type it again: "):
        raise SystemExit("Passwords don't match.")
    return p1


def main(argv):
    ap = argparse.ArgumentParser(prog="run.py")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("adduser")
    a.add_argument("username")
    a.add_argument("--name", default="")
    a.add_argument("--admin", action="store_true")
    a.add_argument("--password-stdin", action="store_true")
    r = sub.add_parser("resetpassword")
    r.add_argument("username")
    r.add_argument("--password-stdin", action="store_true")
    sub.add_parser("agentkey")
    args = ap.parse_args(argv)

    from app import auth, db
    problem = db.check_data_writable()
    if problem:
        raise SystemExit("\n*** " + problem + "\n")
    db.init()
    try:
        if args.cmd == "adduser":
            auth.add_user(args.username, args.name or args.username, read_pw(args.password_stdin), "admin" if args.admin else "member")
            print(f"Added {args.username.lower()} ({'admin' if args.admin else 'member'}).")
        elif args.cmd == "resetpassword":
            auth.set_password(args.username.lower(), read_pw(args.password_stdin))
            print("Password changed.")
        elif args.cmd == "agentkey":
            print(auth.agent_key())
        else:
            serve()
    except ValueError as e:
        raise SystemExit(str(e))


def serve():
    import uvicorn
    from app import db
    host = os.environ.get("GT_HOST", "0.0.0.0")
    print("Greentek Catalog is starting.\n")
    public = os.environ.get("GT_PUBLIC_URL")
    if public:
        print(f"  Open:   {public}")
    else:
        print(f"  On this PC:        http://localhost:{db.PORT}")
        print(f"  Backups:           {db.BACKUP_DIR} (one per day, newest 7 kept)")
        if host == "0.0.0.0":
            for ip in local_ips():
                print(f"  Other office PCs:  http://{ip}:{db.PORT}")
    print("\nKeep this window open. Closing it stops the app for everyone.\n")
    uvicorn.run("app.main:app", host=host, port=db.PORT, log_level="warning",
                proxy_headers=True, forwarded_allow_ips=os.environ.get("GT_TRUSTED_PROXY", "127.0.0.1"))


if __name__ == "__main__":
    main(sys.argv[1:])
