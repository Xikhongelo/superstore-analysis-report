#!/usr/bin/env python3
"""Create public.orders in an existing local PostgreSQL database from Superstore data."""

import argparse
import csv
import getpass
import ipaddress
import os
import re
import sys
import warnings
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import BadZipFile

COLUMNS = (
    "row_id", "order_id", "order_date", "ship_date", "ship_mode",
    "customer_id", "customer_name", "segment", "country", "city", "state",
    "postal_code", "region", "product_id", "category", "sub_category",
    "product_name", "sales", "quantity", "discount", "profit",
)
SQL_TYPES = {
    "row_id": "integer PRIMARY KEY", "order_date": "date NOT NULL",
    "ship_date": "date NOT NULL", "postal_code": "text",
    "sales": "numeric NOT NULL", "quantity": "integer NOT NULL",
    "discount": "numeric NOT NULL", "profit": "numeric NOT NULL",
}
TABLE = "public.orders"


class LoadError(Exception):
    pass


def column_name(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def postgres_config():
    host = os.environ.get("PGHOST") or "localhost"
    address = os.environ.get("PGHOSTADDR")
    port = os.environ.get("PGPORT") or "5432"
    dbname = os.environ.get("PGDATABASE") or "superstore"
    user = os.environ.get("PGUSER") or getpass.getuser()

    def loopback(value):
        try:
            return ipaddress.ip_address(value).is_loopback
        except ValueError:
            return False

    if os.environ.get("PGSERVICE"):
        raise LoadError("Unset PGSERVICE and use explicit local PostgreSQL connection settings.")
    if "," in host or not (host.lower() == "localhost" or loopback(host) or host.startswith("/")):
        raise LoadError("PGHOST must be localhost, a loopback IP, or a local Unix socket directory.")
    if address and (host.startswith("/") or not loopback(address)):
        raise LoadError("Unset PGHOSTADDR or use a loopback IP with a local TCP host.")
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise LoadError("PGPORT must be a port number between 1 and 65535.")
    # libpq must treat the database setting as a name, never a nested DSN.
    if "=" in dbname or dbname.lower().startswith(("postgresql://", "postgres://")):
        raise LoadError("PGDATABASE must be a database name, not a connection string.")
    cfg = {"host": host, "port": port, "dbname": dbname, "user": user, "connect_timeout": 5}
    if address:
        cfg["hostaddr"] = address
    return cfg


def decimal_value(value):
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("expected a plain number") from None
    if not parsed.is_finite():
        raise ValueError("expected a finite number")
    return parsed


def parse_cell(key, value, date_order):
    if value is None or (isinstance(value, str) and not value.strip()):
        if key == "postal_code":
            return None
        raise ValueError("value is blank (or an Excel formula has no saved result)")
    if key in {"row_id", "quantity"}:
        n = decimal_value(value)
        if n != n.to_integral_value() or not 1 <= n <= 2_147_483_647:
            raise ValueError("expected a positive 32-bit integer")
        return int(n)
    if key in {"sales", "discount", "profit"}:
        n = decimal_value(value)
        if key == "discount" and not (0 <= n <= 1):
            raise ValueError("expected a fraction between 0 and 1 (0.30 means 30%)")
        return n
    if key in {"order_date", "ship_date"}:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        raw = str(value).strip()
        try:
            return date.fromisoformat(raw.replace("/", "-") if raw[:4].isdigit() and raw[4:5] == "/" else raw)
        except ValueError:
            try:
                return datetime.strptime(raw, "%m/%d/%Y" if date_order == "mdy" else "%d/%m/%Y").date()
            except ValueError:
                raise ValueError(
                    f"expected an Excel date, ISO YYYY-MM-DD, or slash date in {date_order.upper()} order"
                ) from None
    if key == "postal_code" and not isinstance(value, str):
        n = decimal_value(value)
        if n != n.to_integral_value() or not 0 <= n <= 99999:
            raise ValueError("expected a five-digit US ZIP code or a text postal code")
        return str(int(n)).zfill(5)  # Excel may have stored a US ZIP as a number.
    cleaned = str(value).strip()
    if "\x00" in cleaned:
        raise ValueError("contains a NUL character")
    return cleaned


@contextmanager
def workbook_rows(path, sheet):
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError:
        raise LoadError("Install the project dependencies: python -m pip install -r requirements.txt") from None
    try:
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet not in book.sheetnames:
                raise LoadError(
                    f"Worksheet {sheet!r} not found. Available: {', '.join(book.sheetnames)}. Use --sheet to select one."
                )
            yield book[sheet].iter_rows(values_only=True)
        finally:
            book.close()
    except (BadZipFile, InvalidFileException, KeyError):
        raise LoadError("Could not read the workbook. Resave it as an ordinary .xlsx file and try again.") from None


def csv_encoding(path):
    # Some Superstore exports use Windows-1252. Decode the whole file once
    # before parsing to avoid partially loading a wrongly decoded CSV.
    try:
        path.read_bytes().decode("utf-8-sig")
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"


def read_orders(rows, date_order, is_csv=False):
    try:
        header = next(rows)
    except StopIteration:
        raise LoadError("The selected sheet or CSV is empty.") from None
    indices = {}
    for i, value in enumerate(header):
        key = column_name(value)
        if key not in COLUMNS:
            continue
        if key in indices:
            raise LoadError(f"Duplicate header: {key}.")
        indices[key] = i
    missing = [key.replace("_", " ") for key in COLUMNS if key not in indices]
    if missing:
        raise LoadError(f"Missing expected headers on row 1: {', '.join(missing)}.")
    records, row_ids = [], set()
    for number, row in enumerate(rows, start=2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        if is_csv and len(row) != len(header):
            raise LoadError(f"CSV row {number} has {len(row)} fields; the header has {len(header)}.")
        converted = []
        for key in COLUMNS:
            col = indices[key]
            value = row[col] if col < len(row) else None
            try:
                converted.append(parse_cell(key, value, date_order))
            except ValueError as err:
                raise LoadError(f"Row {number}, {key}: {err}.") from None
        row_id = converted[0]
        if row_id in row_ids:
            raise LoadError(f"Row {number}: duplicate Row ID.")
        row_ids.add(row_id)
        records.append(tuple(converted))
    if not records:
        raise LoadError("Found the headers but no data rows.")
    return records


def read_file(path, sheet, date_order):
    suffix = path.suffix.lower()
    if suffix == ".csv":
        if sheet is not None:
            raise LoadError("--sheet is for Excel files; a CSV does not have worksheets.")
        try:
            with path.open(encoding=csv_encoding(path), newline="") as stream:
                return read_orders(iter(csv.reader(stream, strict=True)), date_order, is_csv=True)
        except UnicodeDecodeError:
            raise LoadError("Could not decode the CSV. Resave it as UTF-8 CSV.") from None
        except csv.Error as err:
            raise LoadError(f"Invalid comma-separated CSV ({err}).") from None
    if suffix == ".xlsx":
        with workbook_rows(path, sheet or "Orders") as rows:
            return read_orders(iter(rows), date_order)
    raise LoadError("Use an .xlsx workbook or a comma-separated .csv file.")


def load_postgres(rows, ask_password):
    try:
        import psycopg
    except ImportError:
        raise LoadError("Install the project dependencies: python -m pip install -r requirements.txt") from None

    cfg = postgres_config()
    if ask_password:
        if not sys.stdin.isatty():
            raise LoadError("--ask-password requires an interactive terminal. Alternatively use PostgreSQL's password file.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                cfg["password"] = getpass.getpass("Local PostgreSQL password: ")
        except (getpass.GetPassWarning, EOFError):
            raise LoadError("Could not prompt securely. Use PostgreSQL's password file instead.") from None

    fields = ", ".join(f"{key} {SQL_TYPES.get(key, 'text NOT NULL')}" for key in COLUMNS)
    columns = ", ".join(COLUMNS)
    try:
        with psycopg.connect(**cfg) as connection:
            with connection.cursor() as cursor:
                # A plain CREATE deliberately fails if the table already exists,
                # even when it is empty or another importer creates it first.
                cursor.execute(f"CREATE TABLE {TABLE} ({fields})")
                with cursor.copy(f"COPY {TABLE} ({columns}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
                cursor.execute(f"SELECT COUNT(*) FROM {TABLE}")
                count = cursor.fetchone()[0]
                if count != len(rows):
                    raise LoadError("Row-count check failed. The import was rolled back.")
    except psycopg.errors.DuplicateTable:
        raise LoadError(
            f"{TABLE} already exists. Nothing was changed. Use a fresh database for an independent reload."
        ) from None
    except psycopg.OperationalError:
        raise LoadError(
            "PostgreSQL connection or session failed. Check that local PostgreSQL is running, the "
            "database already exists, and the port, username and authentication are correct. "
            "If the connection dropped at commit, inspect public.orders before retrying."
        ) from None
    except psycopg.Error as err:
        state = err.sqlstate or "unknown"
        raise LoadError(
            f"PostgreSQL rejected the import (SQLSTATE {state}); check permissions to create a table "
            f"in public and write to {TABLE}. The transaction was rolled back."
        ) from None
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="path to Sample Superstore .xlsx or .csv")
    parser.add_argument("--sheet", help="worksheet name (Excel only; defaults to Orders)")
    parser.add_argument(
        "--date-order", choices=("mdy", "dmy"), default="mdy",
        help="slash-separated text dates only (default: US month/day/year; Excel dates and ISO dates are unaffected)",
    )
    parser.add_argument("--ask-password", action="store_true", help="prompt privately for the PostgreSQL password")
    args = parser.parse_args()
    try:
        records = read_file(args.source.expanduser(), args.sheet, args.date_order)
        count = load_postgres(records, args.ask_password)
    except (LoadError, OSError, KeyboardInterrupt) as err:
        print(f"Load failed: {err or 'interrupted'}", file=sys.stderr)
        return 1
    print(f"Loaded and counted {count:,} rows in {TABLE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
