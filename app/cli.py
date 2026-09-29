import argparse
import getpass

from sqlalchemy import select

from .db import SessionLocal, User, init_db
from .security import hash_password


def main():
    parser = argparse.ArgumentParser(description="Seedance Review administration")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="Create the first administrator interactively")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        email = args.email.strip().lower()
        if "@" not in email or len(email) > 320:
            parser.error("Invalid email")
        password = getpass.getpass("Admin password (min. 12 characters): ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            parser.error("Passwords do not match")
        password_hash = hash_password(password)
        init_db()
        with SessionLocal() as db:
            if db.scalar(select(User).where(User.email == email)):
                parser.error("User already exists")
            db.add(User(email=email, name=args.name.strip(), password_hash=password_hash, is_admin=True))
            db.commit()
        print(f"Administrator created: {email}")


if __name__ == "__main__":
    main()
