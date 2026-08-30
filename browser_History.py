import os
import csv
import json
import sqlite3
import tempfile
import zipfile
import shutil
import datetime


# =============================================================================
# Browser Triage Collector
# Production-oriented Windows browser triage for Cortex XDR Endpoint Scripts.
#
# Target runtime:
#   Python 3.7+
#
# Uses only Python standard-library modules.
#
# Collects:
#   - History / visits
#   - Downloads
#   - Search terms when explicitly stored by Chromium
#   - Visit transitions and from_visit identifiers
#   - Bookmarks
#   - Session artifact metadata (no heavy/binary session parsing)
#   - Profile / artifact inventory and per-artifact status
#
# Does NOT collect:
#   - Cookies or decrypted cookie values
#   - Login Data / passwords
#   - Autofill / payment data
#   - Cache content
#   - IndexedDB / Local Storage
# =============================================================================

COLLECTOR_NAME = "Browser Triage Collector"
COLLECTOR_VERSION = "2.0"

OUTPUT_PREFIX = "browser_triage_"


EXCLUDED_USERS = {
    "Default",
    "Default User",
    "Public",
    "All Users",
    "desktop.ini"
}


# =============================================================================
# Time helpers
# =============================================================================

def utc_now():
    """
    UTC ISO-like timestamp.
    Compatible with Python 3.7 and avoids utcnow() deprecation warnings
    on newer Python releases.
    """
    return datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run_timestamp():
    return datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y%m%d_%H%M%S")


def chromium_time_to_utc(value):
    """
    Chromium/WebKit timestamp:
    microseconds since 1601-01-01 UTC.
    """
    if value is None:
        return ""

    try:
        value = int(value)

        if value <= 0:
            return ""

        epoch = datetime.datetime(1601, 1, 1)

        dt = epoch + datetime.timedelta(
            microseconds=value
        )

        return dt.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

    except Exception:
        return ""


def firefox_time_to_utc(value):
    """
    Firefox timestamps commonly use microseconds since Unix epoch.
    """
    if value is None:
        return ""

    try:
        value = int(value)

        if value <= 0:
            return ""

        dt = datetime.datetime.fromtimestamp(
            value / 1000000.0,
            datetime.timezone.utc
        )

        return dt.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

    except Exception:
        return ""


def unix_time_to_utc(value):
    if value is None:
        return ""

    try:
        dt = datetime.datetime.fromtimestamp(
            float(value),
            datetime.timezone.utc
        )

        return dt.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

    except Exception:
        return ""


# =============================================================================
# Generic helpers
# =============================================================================

def get_hostname():
    return os.environ.get(
        "COMPUTERNAME",
        "UNKNOWN_HOST"
    )


def record_error(
    errors,
    scope,
    path,
    error
):
    errors.append({
        "scope": scope,
        "path": path,
        "error": str(error)
    })


def add_status(
    statuses,
    user,
    browser,
    profile,
    artifact,
    source_path,
    status,
    records=0,
    detail=""
):
    statuses.append({
        "user": user,
        "browser": browser,
        "profile": profile,
        "artifact": artifact,
        "source_path": source_path,
        "status": status,
        "records": records,
        "detail": detail
    })


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def safe_filename(value):
    value = str(value)

    for char in (
        "\\",
        "/",
        ":",
        "*",
        "?",
        '"',
        "<",
        ">",
        "|"
    ):
        value = value.replace(
            char,
            "_"
        )

    return value


# =============================================================================
# User discovery
# =============================================================================

def get_users_root():
    """
    Return absolute Windows Users root.

    Do not use os.path.join("C:", "Users") because on Windows that can
    produce drive-relative C:Users instead of C:\\Users.
    """
    drive = os.environ.get(
        "SystemDrive",
        "C:"
    )

    drive = drive.rstrip(
        "\\/"
    )

    return drive + "\\Users"


def discover_users(errors):
    """
    Enumerate profile directories under C:\\Users and also include
    USERPROFILE as a fallback if available.

    This helps both normal local testing and SYSTEM execution.
    """
    discovered = {}
    users_root = get_users_root()

    if os.path.isdir(users_root):
        try:
            for name in os.listdir(
                users_root
            ):
                if name in EXCLUDED_USERS:
                    continue

                path = os.path.join(
                    users_root,
                    name
                )

                try:
                    if os.path.isdir(path):
                        discovered[
                            os.path.normcase(
                                os.path.abspath(path)
                            )
                        ] = (
                            name,
                            path
                        )

                except Exception as exc:
                    record_error(
                        errors,
                        "User Discovery",
                        path,
                        exc
                    )

        except Exception as exc:
            record_error(
                errors,
                "User Discovery",
                users_root,
                exc
            )

    else:
        record_error(
            errors,
            "User Discovery",
            users_root,
            "Users root not found"
        )

    # Fallback / validation for local execution.
    current_profile = os.environ.get(
        "USERPROFILE"
    )

    if (
        current_profile
        and os.path.isdir(
            current_profile
        )
    ):
        profile_name = os.path.basename(
            os.path.normpath(
                current_profile
            )
        )

        if profile_name not in EXCLUDED_USERS:
            discovered[
                os.path.normcase(
                    os.path.abspath(
                        current_profile
                    )
                )
            ] = (
                profile_name,
                current_profile
            )

    users = list(
        discovered.values()
    )

    users.sort(
        key=lambda item: item[0].lower()
    )

    return users


# =============================================================================
# Browser discovery
# =============================================================================

def chromium_browser_roots(user_path):
    """
    Classic Chromium-family browsers useful for enterprise triage.
    """
    return [
        (
            "Chrome",
            os.path.join(
                user_path,
                "AppData",
                "Local",
                "Google",
                "Chrome",
                "User Data"
            )
        ),
        (
            "Edge",
            os.path.join(
                user_path,
                "AppData",
                "Local",
                "Microsoft",
                "Edge",
                "User Data"
            )
        ),
        (
            "Brave",
            os.path.join(
                user_path,
                "AppData",
                "Local",
                "BraveSoftware",
                "Brave-Browser",
                "User Data"
            )
        )
    ]


def discover_chromium_profiles(
    username,
    browser,
    browser_root,
    profile_inventory,
    statuses,
    errors
):
    profiles = []

    if not os.path.isdir(
        browser_root
    ):
        return profiles

    try:
        names = os.listdir(
            browser_root
        )

    except Exception as exc:
        record_error(
            errors,
            browser + " Profile Discovery",
            browser_root,
            exc
        )

        add_status(
            statuses,
            username,
            browser,
            "",
            "profile_discovery",
            browser_root,
            "error",
            0,
            str(exc)
        )

        return profiles

    for name in names:
        profile_path = os.path.join(
            browser_root,
            name
        )

        try:
            if not os.path.isdir(
                profile_path
            ):
                continue

            history_path = os.path.join(
                profile_path,
                "History"
            )

            bookmarks_path = os.path.join(
                profile_path,
                "Bookmarks"
            )

            sessions_path = os.path.join(
                profile_path,
                "Sessions"
            )

            has_history = os.path.isfile(
                history_path
            )

            has_bookmarks = os.path.isfile(
                bookmarks_path
            )

            has_sessions = os.path.isdir(
                sessions_path
            )

            if not (
                has_history
                or has_bookmarks
                or has_sessions
            ):
                continue

            profiles.append(
                (
                    name,
                    profile_path
                )
            )

            profile_inventory.append({
                "user": username,
                "browser": browser,
                "profile": name,
                "profile_path": profile_path,
                "history_exists": has_history,
                "bookmarks_exists": has_bookmarks,
                "sessions_exists": has_sessions
            })

        except Exception as exc:
            record_error(
                errors,
                browser + " Profile Discovery",
                profile_path,
                exc
            )

    profiles.sort(
        key=lambda item: item[0].lower()
    )

    return profiles


def discover_firefox_profiles(
    username,
    user_path,
    profile_inventory,
    statuses,
    errors
):
    profiles_root = os.path.join(
        user_path,
        "AppData",
        "Roaming",
        "Mozilla",
        "Firefox",
        "Profiles"
    )

    profiles = []

    if not os.path.isdir(
        profiles_root
    ):
        return profiles

    try:
        names = os.listdir(
            profiles_root
        )

    except Exception as exc:
        record_error(
            errors,
            "Firefox Profile Discovery",
            profiles_root,
            exc
        )

        add_status(
            statuses,
            username,
            "Firefox",
            "",
            "profile_discovery",
            profiles_root,
            "error",
            0,
            str(exc)
        )

        return profiles

    for name in names:
        profile_path = os.path.join(
            profiles_root,
            name
        )

        try:
            if not os.path.isdir(
                profile_path
            ):
                continue

            places_path = os.path.join(
                profile_path,
                "places.sqlite"
            )

            session_file = os.path.join(
                profile_path,
                "sessionstore.jsonlz4"
            )

            session_dir = os.path.join(
                profile_path,
                "sessionstore-backups"
            )

            has_places = os.path.isfile(
                places_path
            )

            has_sessions = (
                os.path.isfile(
                    session_file
                )
                or os.path.isdir(
                    session_dir
                )
            )

            if not (
                has_places
                or has_sessions
            ):
                continue

            profiles.append(
                (
                    name,
                    profile_path
                )
            )

            profile_inventory.append({
                "user": username,
                "browser": "Firefox",
                "profile": name,
                "profile_path": profile_path,
                "history_exists": has_places,
                "bookmarks_exists": has_places,
                "sessions_exists": has_sessions
            })

        except Exception as exc:
            record_error(
                errors,
                "Firefox Profile Discovery",
                profile_path,
                exc
            )

    profiles.sort(
        key=lambda item: item[0].lower()
    )

    return profiles


# =============================================================================
# SQLite acquisition / schema helpers
# =============================================================================

def table_exists(
    connection,
    table_name
):
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
          AND name=?
        """,
        (
            table_name,
        )
    )

    return (
        cursor.fetchone()
        is not None
    )


def table_columns(
    connection,
    table_name
):
    columns = set()

    cursor = connection.cursor()

    cursor.execute(
        "PRAGMA table_info({})".format(
            table_name
        )
    )

    for row in cursor.fetchall():
        columns.add(
            row[1]
        )

    return columns


def copy_sqlite_database(
    source_path,
    destination_dir,
    errors,
    scope
):
    """
    Copy SQLite DB + WAL/SHM into collector workspace.

    The browser's source DB is never opened for writing.
    """
    if not os.path.isfile(
        source_path
    ):
        return None

    try:
        ensure_dir(
            destination_dir
        )

        copied_path = os.path.join(
            destination_dir,
            os.path.basename(
                source_path
            )
        )

        shutil.copyfile(
            source_path,
            copied_path
        )

    except Exception as exc:
        record_error(
            errors,
            scope + " Copy",
            source_path,
            exc
        )

        return None

    for suffix in (
        "-wal",
        "-shm"
    ):
        source_sidecar = (
            source_path
            + suffix
        )

        if not os.path.isfile(
            source_sidecar
        ):
            continue

        try:
            shutil.copyfile(
                source_sidecar,
                copied_path + suffix
            )

        except Exception as exc:
            # Sidecars are best effort. Main DB copy may still be parseable.
            record_error(
                errors,
                scope + " Sidecar",
                source_sidecar,
                exc
            )

    return copied_path


def open_sqlite_readonly(path):
    normalized = path.replace(
        "\\",
        "/"
    )

    uri = "file:{}?mode=ro".format(
        normalized
    )

    return sqlite3.connect(
        uri,
        uri=True,
        timeout=5
    )


# =============================================================================
# CSV helpers
# =============================================================================

def open_csv_writer(
    path,
    fields
):
    """
    utf-8-sig gives Excel a UTF-8 BOM and makes direct opening cleaner.
    """
    handle = open(
        path,
        "w",
        newline="",
        encoding="utf-8-sig"
    )

    writer = csv.DictWriter(
        handle,
        fieldnames=fields,
        extrasaction="ignore"
    )

    writer.writeheader()

    return (
        handle,
        writer
    )


def write_rows(
    path,
    fields,
    rows
):
    handle, writer = open_csv_writer(
        path,
        fields
    )

    try:
        for row in rows:
            writer.writerow(
                row
            )

    finally:
        handle.close()


# =============================================================================
# Chromium transition decoding
# =============================================================================

CHROMIUM_TRANSITIONS = {
    0: "link",
    1: "typed",
    2: "auto_bookmark",
    3: "auto_subframe",
    4: "manual_subframe",
    5: "generated",
    6: "auto_toplevel",
    7: "form_submit",
    8: "reload",
    9: "keyword",
    10: "keyword_generated"
}


def decode_chromium_transition(
    raw_value
):
    if raw_value is None:
        return ""

    try:
        value = int(
            raw_value
        )

        base = CHROMIUM_TRANSITIONS.get(
            value & 0xFF,
            "unknown"
        )

        qualifiers = []

        if value & 0x01000000:
            qualifiers.append(
                "forward_back"
            )

        if value & 0x02000000:
            qualifiers.append(
                "from_address_bar"
            )

        if value & 0x10000000:
            qualifiers.append(
                "chain_start"
            )

        if value & 0x20000000:
            qualifiers.append(
                "chain_end"
            )

        if value & 0x40000000:
            qualifiers.append(
                "client_redirect"
            )

        if value & 0x80000000:
            qualifiers.append(
                "server_redirect"
            )

        if qualifiers:
            return (
                base
                + "|"
                + "|".join(
                    qualifiers
                )
            )

        return base

    except Exception:
        return ""


FIREFOX_VISIT_TYPES = {
    1: "link",
    2: "typed",
    3: "bookmark",
    4: "embed",
    5: "redirect_permanent",
    6: "redirect_temporary",
    7: "download",
    8: "framed_link",
    9: "reload"
}


def decode_firefox_visit_type(
    value
):
    try:
        return FIREFOX_VISIT_TYPES.get(
            int(value),
            "unknown"
        )

    except Exception:
        return ""


# =============================================================================
# Chromium History
# =============================================================================

def collect_chromium_history(
    username,
    browser,
    profile,
    profile_path,
    profile_work,
    writer,
    statuses,
    errors
):
    source = os.path.join(
        profile_path,
        "History"
    )

    if not os.path.isfile(
        source
    ):
        add_status(
            statuses,
            username,
            browser,
            profile,
            "history",
            source,
            "not_present"
        )

        return 0

    copied = copy_sqlite_database(
        source,
        os.path.join(
            profile_work,
            "history"
        ),
        errors,
        browser + " History"
    )

    if not copied:
        add_status(
            statuses,
            username,
            browser,
            profile,
            "history",
            source,
            "copy_failed"
        )

        return 0

    connection = None
    count = 0

    try:
        connection = open_sqlite_readonly(
            copied
        )

        if not table_exists(
            connection,
            "urls"
        ):
            raise RuntimeError(
                "Missing urls table"
            )

        if not table_exists(
            connection,
            "visits"
        ):
            raise RuntimeError(
                "Missing visits table"
            )

        url_columns = table_columns(
            connection,
            "urls"
        )

        visit_columns = table_columns(
            connection,
            "visits"
        )

        if (
            "id" not in url_columns
            or "url" not in url_columns
            or "url" not in visit_columns
        ):
            raise RuntimeError(
                "Unsupported History schema"
            )

        def url_col(
            name,
            fallback="NULL"
        ):
            if name in url_columns:
                return "u.{}".format(
                    name
                )

            return fallback

        def visit_col(
            name,
            fallback="NULL"
        ):
            if name in visit_columns:
                return "v.{}".format(
                    name
                )

            return fallback

        order_expression = visit_col(
            "visit_time",
            "v.rowid"
        )

        query = """
        SELECT
            {visit_id},
            {visit_time},
            {from_visit},
            {transition},
            {url},
            {title},
            {visit_count},
            {typed_count},
            {last_visit_time}
        FROM visits v
        JOIN urls u
          ON v.url = u.id
        ORDER BY {order_expression}
        """.format(
            visit_id=visit_col(
                "id"
            ),
            visit_time=visit_col(
                "visit_time"
            ),
            from_visit=visit_col(
                "from_visit"
            ),
            transition=visit_col(
                "transition"
            ),
            url=url_col(
                "url"
            ),
            title=url_col(
                "title"
            ),
            visit_count=url_col(
                "visit_count"
            ),
            typed_count=url_col(
                "typed_count"
            ),
            last_visit_time=url_col(
                "last_visit_time"
            ),
            order_expression=order_expression
        )

        cursor = connection.cursor()

        cursor.execute(
            query
        )

        while True:
            rows = cursor.fetchmany(
                1000
            )

            if not rows:
                break

            for row in rows:
                writer.writerow({
                    "user": username,
                    "browser": browser,
                    "profile": profile,
                    "visit_id": row[0],
                    "visit_time_utc":
                        chromium_time_to_utc(
                            row[1]
                        ),
                    "from_visit_id": row[2],
                    "transition":
                        decode_chromium_transition(
                            row[3]
                        ),
                    "raw_transition": row[3],
                    "url": row[4],
                    "title": row[5],
                    "visit_count": row[6],
                    "typed_count": row[7],
                    "last_visit_time_utc":
                        chromium_time_to_utc(
                            row[8]
                        ),
                    "source_path": source
                })

                count += 1

        add_status(
            statuses,
            username,
            browser,
            profile,
            "history",
            source,
            "parsed",
            count
        )

    except Exception as exc:
        record_error(
            errors,
            browser + " History",
            source,
            exc
        )

        add_status(
            statuses,
            username,
            browser,
            profile,
            "history",
            source,
            "parse_failed",
            count,
            str(exc)
        )

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    return count


# =============================================================================
# Chromium Downloads
# =============================================================================

def collect_chromium_downloads(
    username,
    browser,
    profile,
    profile_path,
    profile_work,
    writer,
    statuses,
    errors
):
    source = os.path.join(
        profile_path,
        "History"
    )

    if not os.path.isfile(
        source
    ):
        add_status(
            statuses,
            username,
            browser,
            profile,
            "downloads",
            source,
            "not_present"
        )

        return 0

    copied = copy_sqlite_database(
        source,
        os.path.join(
            profile_work,
            "downloads"
        ),
        errors,
        browser + " Downloads"
    )

    if not copied:
        add_status(
            statuses,
            username,
            browser,
            profile,
            "downloads",
            source,
            "copy_failed"
        )

        return 0

    connection = None
    count = 0

    try:
        connection = open_sqlite_readonly(
            copied
        )

        if not table_exists(
            connection,
            "downloads"
        ):
            add_status(
                statuses,
                username,
                browser,
                profile,
                "downloads",
                source,
                "table_not_present"
            )

            return 0

        columns = table_columns(
            connection,
            "downloads"
        )

        def dcol(
            name,
            fallback="NULL"
        ):
            if name in columns:
                return "d.{}".format(
                    name
                )

            return fallback

        target_path = dcol(
            "target_path",
            dcol(
                "full_path"
            )
        )

        query = """
        SELECT
            {id},
            {target_path},
            {start_time},
            {end_time},
            {received_bytes},
            {total_bytes},
            {state},
            {danger_type},
            {interrupt_reason},
            {opened},
            {last_access_time},
            {referrer},
            {tab_url},
            {tab_referrer_url},
            {site_url}
        FROM downloads d
        """.format(
            id=dcol(
                "id"
            ),
            target_path=target_path,
            start_time=dcol(
                "start_time"
            ),
            end_time=dcol(
                "end_time"
            ),
            received_bytes=dcol(
                "received_bytes"
            ),
            total_bytes=dcol(
                "total_bytes"
            ),
            state=dcol(
                "state"
            ),
            danger_type=dcol(
                "danger_type"
            ),
            interrupt_reason=dcol(
                "interrupt_reason"
            ),
            opened=dcol(
                "opened"
            ),
            last_access_time=dcol(
                "last_access_time"
            ),
            referrer=dcol(
                "referrer"
            ),
            tab_url=dcol(
                "tab_url"
            ),
            tab_referrer_url=dcol(
                "tab_referrer_url"
            ),
            site_url=dcol(
                "site_url"
            )
        )

        cursor = connection.cursor()

        cursor.execute(
            query
        )

        has_chains = table_exists(
            connection,
            "downloads_url_chains"
        )

        chain_columns = set()

        if has_chains:
            chain_columns = table_columns(
                connection,
                "downloads_url_chains"
            )

        while True:
            rows = cursor.fetchmany(
                500
            )

            if not rows:
                break

            for row in rows:
                download_id = row[0]

                source_url = ""
                final_url = ""

                if (
                    has_chains
                    and "id" in chain_columns
                    and "url" in chain_columns
                ):
                    try:
                        chain_cursor = (
                            connection.cursor()
                        )

                        order_clause = ""

                        if (
                            "chain_index"
                            in chain_columns
                        ):
                            order_clause = (
                                " ORDER BY chain_index"
                            )

                        chain_cursor.execute(
                            """
                            SELECT url
                            FROM downloads_url_chains
                            WHERE id = ?
                            {}
                            """.format(
                                order_clause
                            ),
                            (
                                download_id,
                            )
                        )

                        chain_urls = []

                        for chain_row in (
                            chain_cursor.fetchall()
                        ):
                            if chain_row[0]:
                                chain_urls.append(
                                    chain_row[0]
                                )

                        if chain_urls:
                            source_url = (
                                chain_urls[0]
                            )

                            final_url = (
                                chain_urls[-1]
                            )

                    except Exception as exc:
                        record_error(
                            errors,
                            browser
                            + " Download URL Chain",
                            source,
                            exc
                        )

                referrer = (
                    row[11]
                    or ""
                )

                tab_url = (
                    row[12]
                    or ""
                )

                site_url = (
                    row[14]
                    or ""
                )

                if not source_url:
                    source_url = (
                        referrer
                        or tab_url
                        or site_url
                    )

                if not final_url:
                    final_url = (
                        tab_url
                        or source_url
                    )

                writer.writerow({
                    "user": username,
                    "browser": browser,
                    "profile": profile,
                    "download_id": download_id,
                    "source_url": source_url,
                    "final_url": final_url,
                    "referrer": referrer,
                    "tab_url": tab_url,
                    "tab_referrer_url":
                        row[13] or "",
                    "site_url": site_url,
                    "target_path":
                        row[1] or "",
                    "start_time_utc":
                        chromium_time_to_utc(
                            row[2]
                        ),
                    "end_time_utc":
                        chromium_time_to_utc(
                            row[3]
                        ),
                    "last_access_time_utc":
                        chromium_time_to_utc(
                            row[10]
                        ),
                    "received_bytes": row[4],
                    "total_bytes": row[5],
                    "state": row[6],
                    "danger_type": row[7],
                    "interrupt_reason": row[8],
                    "opened": row[9],
                    "source_path": source
                })

                count += 1

        add_status(
            statuses,
            username,
            browser,
            profile,
            "downloads",
            source,
            "parsed",
            count
        )

    except Exception as exc:
        record_error(
            errors,
            browser + " Downloads",
            source,
            exc
        )

        add_status(
            statuses,
            username,
            browser,
            profile,
            "downloads",
            source,
            "parse_failed",
            count,
            str(exc)
        )

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    return count


# =============================================================================
# Chromium Search Terms
# =============================================================================

def collect_chromium_search_terms(
    username,
    browser,
    profile,
    profile_path,
    profile_work,
    writer,
    statuses,
    errors
):
    source = os.path.join(
        profile_path,
        "History"
    )

    if not os.path.isfile(
        source
    ):
        add_status(
            statuses,
            username,
            browser,
            profile,
            "search_terms",
            source,
            "not_present"
        )

        return 0

    copied = copy_sqlite_database(
        source,
        os.path.join(
            profile_work,
            "search_terms"
        ),
        errors,
        browser + " Search Terms"
    )

    if not copied:
        add_status(
            statuses,
            username,
            browser,
            profile,
            "search_terms",
            source,
            "copy_failed"
        )

        return 0

    connection = None
    count = 0

    try:
        connection = open_sqlite_readonly(
            copied
        )

        if not table_exists(
            connection,
            "keyword_search_terms"
        ):
            add_status(
                statuses,
                username,
                browser,
                profile,
                "search_terms",
                source,
                "table_not_present"
            )

            return 0

        search_columns = table_columns(
            connection,
            "keyword_search_terms"
        )

        if "term" not in search_columns:
            raise RuntimeError(
                "keyword_search_terms.term missing"
            )

        if (
            "url_id" in search_columns
            and table_exists(
                connection,
                "urls"
            )
        ):
            query = """
            SELECT
                k.term,
                u.url,
                u.title,
                u.last_visit_time
            FROM keyword_search_terms k
            LEFT JOIN urls u
              ON k.url_id = u.id
            """

        else:
            query = """
            SELECT
                term,
                NULL,
                NULL,
                NULL
            FROM keyword_search_terms
            """

        cursor = connection.cursor()

        cursor.execute(
            query
        )

        while True:
            rows = cursor.fetchmany(
                1000
            )

            if not rows:
                break

            for row in rows:
                writer.writerow({
                    "user": username,
                    "browser": browser,
                    "profile": profile,
                    "search_term": row[0],
                    "url": row[1],
                    "title": row[2],
                    "related_last_visit_time_utc":
                        chromium_time_to_utc(
                            row[3]
                        ),
                    "source_path": source
                })

                count += 1

        add_status(
            statuses,
            username,
            browser,
            profile,
            "search_terms",
            source,
            "parsed",
            count
        )

    except Exception as exc:
        record_error(
            errors,
            browser + " Search Terms",
            source,
            exc
        )

        add_status(
            statuses,
            username,
            browser,
            profile,
            "search_terms",
            source,
            "parse_failed",
            count,
            str(exc)
        )

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    return count


# =============================================================================
# Chromium Bookmarks
# =============================================================================

def collect_chromium_bookmarks(
    username,
    browser,
    profile,
    profile_path,
    writer,
    statuses,
    errors
):
    source = os.path.join(
        profile_path,
        "Bookmarks"
    )

    if not os.path.isfile(
        source
    ):
        add_status(
            statuses,
            username,
            browser,
            profile,
            "bookmarks",
            source,
            "not_present"
        )

        return 0

    try:
        with open(
            source,
            "r",
            encoding="utf-8-sig",
            errors="replace"
        ) as handle:
            data = json.load(
                handle
            )

    except Exception as exc:
        record_error(
            errors,
            browser + " Bookmarks",
            source,
            exc
        )

        add_status(
            statuses,
            username,
            browser,
            profile,
            "bookmarks",
            source,
            "parse_failed",
            0,
            str(exc)
        )

        return 0

    count = [0]

    def walk(
        node,
        folder_path
    ):
        if not isinstance(
            node,
            dict
        ):
            return

        node_type = node.get(
            "type",
            ""
        )

        node_name = node.get(
            "name",
            ""
        )

        if node_type == "url":
            writer.writerow({
                "user": username,
                "browser": browser,
                "profile": profile,
                "bookmark_name":
                    node_name,
                "url":
                    node.get(
                        "url",
                        ""
                    ),
                "folder":
                    folder_path,
                "folder_id":
                    "",
                "date_added_utc":
                    chromium_time_to_utc(
                        node.get(
                            "date_added"
                        )
                    ),
                "date_modified_utc":
                    chromium_time_to_utc(
                        node.get(
                            "date_modified"
                        )
                    ),
                "source_path":
                    source
            })

            count[0] += 1

            return

        children = node.get(
            "children",
            []
        )

        next_folder = (
            folder_path
        )

        if node_name:
            if next_folder:
                next_folder = (
                    next_folder
                    + "/"
                    + node_name
                )

            else:
                next_folder = (
                    node_name
                )

        if isinstance(
            children,
            list
        ):
            for child in children:
                walk(
                    child,
                    next_folder
                )

    roots = data.get(
        "roots",
        {}
    )

    if isinstance(
        roots,
        dict
    ):
        for root_name in roots:
            walk(
                roots[root_name],
                root_name
            )

    add_status(
        statuses,
        username,
        browser,
        profile,
        "bookmarks",
        source,
        "parsed",
        count[0]
    )

    return count[0]


# =============================================================================
# Chromium Session Metadata
# =============================================================================

def collect_chromium_sessions(
    username,
    browser,
    profile,
    profile_path,
    writer,
    statuses,
    errors
):
    source = os.path.join(
        profile_path,
        "Sessions"
    )

    if not os.path.isdir(
        source
    ):
        add_status(
            statuses,
            username,
            browser,
            profile,
            "sessions",
            source,
            "not_present"
        )

        return 0

    count = 0

    try:
        names = os.listdir(
            source
        )

        for name in names:
            path = os.path.join(
                source,
                name
            )

            if not os.path.isfile(
                path
            ):
                continue

            try:
                stat_info = os.stat(
                    path
                )

                writer.writerow({
                    "user": username,
                    "browser": browser,
                    "profile": profile,
                    "artifact_type":
                        "session_state",
                    "file_name":
                        name,
                    "size_bytes":
                        stat_info.st_size,
                    "modified_time_utc":
                        unix_time_to_utc(
                            stat_info.st_mtime
                        ),
                    "source_path":
                        path
                })

                count += 1

            except Exception as exc:
                record_error(
                    errors,
                    browser + " Sessions",
                    path,
                    exc
                )

        add_status(
            statuses,
            username,
            browser,
            profile,
            "sessions",
            source,
            "metadata_collected",
            count
        )

    except Exception as exc:
        record_error(
            errors,
            browser + " Sessions",
            source,
            exc
        )

        add_status(
            statuses,
            username,
            browser,
            profile,
            "sessions",
            source,
            "enumeration_failed",
            count,
            str(exc)
        )

    return count


# =============================================================================
# Firefox History + download visit extraction
# =============================================================================

def collect_firefox_places(
    username,
    profile,
    profile_path,
    profile_work,
    history_writer,
    download_writer,
    statuses,
    errors
):
    source = os.path.join(
        profile_path,
        "places.sqlite"
    )

    if not os.path.isfile(
        source
    ):
        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "history",
            source,
            "not_present"
        )

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "downloads",
            source,
            "not_present"
        )

        return (
            0,
            0
        )

    copied = copy_sqlite_database(
        source,
        os.path.join(
            profile_work,
            "places"
        ),
        errors,
        "Firefox Places"
    )

    if not copied:
        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "history",
            source,
            "copy_failed"
        )

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "downloads",
            source,
            "copy_failed"
        )

        return (
            0,
            0
        )

    connection = None
    history_count = 0
    download_count = 0

    try:
        connection = open_sqlite_readonly(
            copied
        )

        if not table_exists(
            connection,
            "moz_places"
        ):
            raise RuntimeError(
                "Missing moz_places table"
            )

        if not table_exists(
            connection,
            "moz_historyvisits"
        ):
            raise RuntimeError(
                "Missing moz_historyvisits table"
            )

        visit_columns = table_columns(
            connection,
            "moz_historyvisits"
        )

        from_visit = "NULL"

        if "from_visit" in visit_columns:
            from_visit = (
                "h.from_visit"
            )

        query = """
        SELECT
            h.id,
            h.visit_date,
            h.visit_type,
            {from_visit},
            p.url,
            p.title,
            p.visit_count,
            p.last_visit_date
        FROM moz_historyvisits h
        JOIN moz_places p
          ON h.place_id = p.id
        ORDER BY h.visit_date
        """.format(
            from_visit=from_visit
        )

        cursor = connection.cursor()

        cursor.execute(
            query
        )

        while True:
            rows = cursor.fetchmany(
                1000
            )

            if not rows:
                break

            for row in rows:
                visit_type = row[2]

                history_writer.writerow({
                    "user": username,
                    "browser": "Firefox",
                    "profile": profile,
                    "visit_id": row[0],
                    "visit_time_utc":
                        firefox_time_to_utc(
                            row[1]
                        ),
                    "from_visit_id":
                        row[3],
                    "transition":
                        decode_firefox_visit_type(
                            visit_type
                        ),
                    "raw_transition":
                        visit_type,
                    "url":
                        row[4],
                    "title":
                        row[5],
                    "visit_count":
                        row[6],
                    "typed_count":
                        "",
                    "last_visit_time_utc":
                        firefox_time_to_utc(
                            row[7]
                        ),
                    "source_path":
                        source
                })

                history_count += 1

                # Firefox visit_type 7 = download.
                # This safely identifies a download visit but does not invent
                # a target path that the artifact does not provide here.
                try:
                    is_download = (
                        int(visit_type)
                        == 7
                    )
                except Exception:
                    is_download = False

                if is_download:
                    download_writer.writerow({
                        "user": username,
                        "browser": "Firefox",
                        "profile": profile,
                        "download_id":
                            row[0],
                        "source_url":
                            row[4],
                        "final_url":
                            row[4],
                        "referrer":
                            "",
                        "tab_url":
                            "",
                        "tab_referrer_url":
                            "",
                        "site_url":
                            "",
                        "target_path":
                            "",
                        "start_time_utc":
                            firefox_time_to_utc(
                                row[1]
                            ),
                        "end_time_utc":
                            "",
                        "last_access_time_utc":
                            "",
                        "received_bytes":
                            "",
                        "total_bytes":
                            "",
                        "state":
                            "history_visit_download",
                        "danger_type":
                            "",
                        "interrupt_reason":
                            "",
                        "opened":
                            "",
                        "source_path":
                            source
                    })

                    download_count += 1

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "history",
            source,
            "parsed",
            history_count
        )

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "downloads",
            source,
            "download_visits_extracted",
            download_count,
            "Firefox places visit_type=7; target path not derived"
        )

    except Exception as exc:
        record_error(
            errors,
            "Firefox Places",
            source,
            exc
        )

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "history",
            source,
            "parse_failed",
            history_count,
            str(exc)
        )

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "downloads",
            source,
            "parse_failed",
            download_count,
            str(exc)
        )

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    return (
        history_count,
        download_count
    )


# =============================================================================
# Firefox Bookmarks
# =============================================================================

def collect_firefox_bookmarks(
    username,
    profile,
    profile_path,
    profile_work,
    writer,
    statuses,
    errors
):
    source = os.path.join(
        profile_path,
        "places.sqlite"
    )

    if not os.path.isfile(
        source
    ):
        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "bookmarks",
            source,
            "not_present"
        )

        return 0

    copied = copy_sqlite_database(
        source,
        os.path.join(
            profile_work,
            "bookmarks"
        ),
        errors,
        "Firefox Bookmarks"
    )

    if not copied:
        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "bookmarks",
            source,
            "copy_failed"
        )

        return 0

    connection = None
    count = 0

    try:
        connection = open_sqlite_readonly(
            copied
        )

        if not table_exists(
            connection,
            "moz_bookmarks"
        ):
            add_status(
                statuses,
                username,
                "Firefox",
                profile,
                "bookmarks",
                source,
                "table_not_present"
            )

            return 0

        if not table_exists(
            connection,
            "moz_places"
        ):
            raise RuntimeError(
                "Missing moz_places table"
            )

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                b.title,
                p.url,
                b.parent,
                b.dateAdded,
                b.lastModified
            FROM moz_bookmarks b
            JOIN moz_places p
              ON b.fk = p.id
            WHERE b.type = 1
            ORDER BY b.dateAdded
            """
        )

        while True:
            rows = cursor.fetchmany(
                1000
            )

            if not rows:
                break

            for row in rows:
                writer.writerow({
                    "user": username,
                    "browser": "Firefox",
                    "profile": profile,
                    "bookmark_name":
                        row[0],
                    "url":
                        row[1],
                    "folder":
                        "",
                    "folder_id":
                        row[2],
                    "date_added_utc":
                        firefox_time_to_utc(
                            row[3]
                        ),
                    "date_modified_utc":
                        firefox_time_to_utc(
                            row[4]
                        ),
                    "source_path":
                        source
                })

                count += 1

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "bookmarks",
            source,
            "parsed",
            count
        )

    except Exception as exc:
        record_error(
            errors,
            "Firefox Bookmarks",
            source,
            exc
        )

        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "bookmarks",
            source,
            "parse_failed",
            count,
            str(exc)
        )

    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    return count


# =============================================================================
# Firefox Sessions metadata
# =============================================================================

def collect_firefox_sessions(
    username,
    profile,
    profile_path,
    writer,
    statuses,
    errors
):
    candidates = [
        os.path.join(
            profile_path,
            "sessionstore.jsonlz4"
        ),
        os.path.join(
            profile_path,
            "sessionstore-backups"
        )
    ]

    count = 0
    found_any = False

    for candidate in candidates:
        if os.path.isfile(
            candidate
        ):
            found_any = True

            try:
                stat_info = os.stat(
                    candidate
                )

                writer.writerow({
                    "user": username,
                    "browser": "Firefox",
                    "profile": profile,
                    "artifact_type":
                        "session_state",
                    "file_name":
                        os.path.basename(
                            candidate
                        ),
                    "size_bytes":
                        stat_info.st_size,
                    "modified_time_utc":
                        unix_time_to_utc(
                            stat_info.st_mtime
                        ),
                    "source_path":
                        candidate
                })

                count += 1

            except Exception as exc:
                record_error(
                    errors,
                    "Firefox Sessions",
                    candidate,
                    exc
                )

        elif os.path.isdir(
            candidate
        ):
            found_any = True

            try:
                for name in os.listdir(
                    candidate
                ):
                    path = os.path.join(
                        candidate,
                        name
                    )

                    if not os.path.isfile(
                        path
                    ):
                        continue

                    try:
                        stat_info = os.stat(
                            path
                        )

                        writer.writerow({
                            "user": username,
                            "browser": "Firefox",
                            "profile": profile,
                            "artifact_type":
                                "session_state",
                            "file_name":
                                name,
                            "size_bytes":
                                stat_info.st_size,
                            "modified_time_utc":
                                unix_time_to_utc(
                                    stat_info.st_mtime
                                ),
                            "source_path":
                                path
                        })

                        count += 1

                    except Exception as exc:
                        record_error(
                            errors,
                            "Firefox Sessions",
                            path,
                            exc
                        )

            except Exception as exc:
                record_error(
                    errors,
                    "Firefox Sessions",
                    candidate,
                    exc
                )

    if found_any:
        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "sessions",
            profile_path,
            "metadata_collected",
            count
        )

    else:
        add_status(
            statuses,
            username,
            "Firefox",
            profile,
            "sessions",
            profile_path,
            "not_present"
        )

    return count


# =============================================================================
# ZIP
# =============================================================================

def create_zip(
    source_dir,
    output_zip
):
    with zipfile.ZipFile(
        output_zip,
        "w",
        zipfile.ZIP_DEFLATED
    ) as archive:

        for root, dirs, files in os.walk(
            source_dir
        ):
            for filename in files:
                full_path = os.path.join(
                    root,
                    filename
                )

                relative_path = os.path.relpath(
                    full_path,
                    source_dir
                )

                archive.write(
                    full_path,
                    relative_path
                )


# =============================================================================
# Main Cortex entry point
# =============================================================================

def main():
    temp_root = tempfile.gettempdir()
    hostname = get_hostname()

    run_id = "{}_{}".format(
        run_timestamp(),
        os.getpid()
    )

    work_dir = os.path.join(
        temp_root,
        "BrowserTriage_{}".format(
            run_id
        )
    )

    db_work = os.path.join(
        work_dir,
        "_db_work"
    )

    ensure_dir(
        work_dir
    )

    ensure_dir(
        db_work
    )

    output_zip = os.path.join(
        temp_root,
        "{}{}_{}.zip".format(
            OUTPUT_PREFIX,
            safe_filename(
                hostname
            ),
            run_id
        )
    )

    collection_start = utc_now()

    errors = []
    statuses = []
    profile_inventory = []

    counts = {
        "history": 0,
        "downloads": 0,
        "search_terms": 0,
        "bookmarks": 0,
        "sessions": 0
    }

    profile_counts = {
        "Chrome": 0,
        "Edge": 0,
        "Brave": 0,
        "Firefox": 0
    }

    # -------------------------------------------------------------------------
    # CSV schemas
    # -------------------------------------------------------------------------

    history_fields = [
        "user",
        "browser",
        "profile",
        "visit_id",
        "visit_time_utc",
        "from_visit_id",
        "transition",
        "raw_transition",
        "url",
        "title",
        "visit_count",
        "typed_count",
        "last_visit_time_utc",
        "source_path"
    ]

    download_fields = [
        "user",
        "browser",
        "profile",
        "download_id",
        "source_url",
        "final_url",
        "referrer",
        "tab_url",
        "tab_referrer_url",
        "site_url",
        "target_path",
        "start_time_utc",
        "end_time_utc",
        "last_access_time_utc",
        "received_bytes",
        "total_bytes",
        "state",
        "danger_type",
        "interrupt_reason",
        "opened",
        "source_path"
    ]

    search_fields = [
        "user",
        "browser",
        "profile",
        "search_term",
        "url",
        "title",
        "related_last_visit_time_utc",
        "source_path"
    ]

    bookmark_fields = [
        "user",
        "browser",
        "profile",
        "bookmark_name",
        "url",
        "folder",
        "folder_id",
        "date_added_utc",
        "date_modified_utc",
        "source_path"
    ]

    session_fields = [
        "user",
        "browser",
        "profile",
        "artifact_type",
        "file_name",
        "size_bytes",
        "modified_time_utc",
        "source_path"
    ]

    inventory_fields = [
        "user",
        "browser",
        "profile",
        "profile_path",
        "history_exists",
        "bookmarks_exists",
        "sessions_exists"
    ]

    status_fields = [
        "user",
        "browser",
        "profile",
        "artifact",
        "source_path",
        "status",
        "records",
        "detail"
    ]

    error_fields = [
        "scope",
        "path",
        "error"
    ]

    # -------------------------------------------------------------------------
    # Open result streams
    # -------------------------------------------------------------------------

    history_handle, history_writer = open_csv_writer(
        os.path.join(
            work_dir,
            "history.csv"
        ),
        history_fields
    )

    downloads_handle, downloads_writer = open_csv_writer(
        os.path.join(
            work_dir,
            "downloads.csv"
        ),
        download_fields
    )

    search_handle, search_writer = open_csv_writer(
        os.path.join(
            work_dir,
            "search_terms.csv"
        ),
        search_fields
    )

    bookmark_handle, bookmark_writer = open_csv_writer(
        os.path.join(
            work_dir,
            "bookmarks.csv"
        ),
        bookmark_fields
    )

    sessions_handle, sessions_writer = open_csv_writer(
        os.path.join(
            work_dir,
            "sessions.csv"
        ),
        session_fields
    )

    users = discover_users(
        errors
    )

    try:
        for username, user_path in users:

            # -----------------------------------------------------------------
            # Chromium family
            # -----------------------------------------------------------------

            for browser, browser_root in chromium_browser_roots(
                user_path
            ):
                profiles = discover_chromium_profiles(
                    username,
                    browser,
                    browser_root,
                    profile_inventory,
                    statuses,
                    errors
                )

                profile_counts[
                    browser
                ] += len(
                    profiles
                )

                for profile, profile_path in profiles:
                    profile_work = os.path.join(
                        db_work,
                        safe_filename(
                            "{}_{}_{}".format(
                                username,
                                browser,
                                profile
                            )
                        )
                    )

                    counts["history"] += (
                        collect_chromium_history(
                            username,
                            browser,
                            profile,
                            profile_path,
                            profile_work,
                            history_writer,
                            statuses,
                            errors
                        )
                    )

                    counts["downloads"] += (
                        collect_chromium_downloads(
                            username,
                            browser,
                            profile,
                            profile_path,
                            profile_work,
                            downloads_writer,
                            statuses,
                            errors
                        )
                    )

                    counts["search_terms"] += (
                        collect_chromium_search_terms(
                            username,
                            browser,
                            profile,
                            profile_path,
                            profile_work,
                            search_writer,
                            statuses,
                            errors
                        )
                    )

                    counts["bookmarks"] += (
                        collect_chromium_bookmarks(
                            username,
                            browser,
                            profile,
                            profile_path,
                            bookmark_writer,
                            statuses,
                            errors
                        )
                    )

                    counts["sessions"] += (
                        collect_chromium_sessions(
                            username,
                            browser,
                            profile,
                            profile_path,
                            sessions_writer,
                            statuses,
                            errors
                        )
                    )

            # -----------------------------------------------------------------
            # Firefox
            # -----------------------------------------------------------------

            firefox_profiles = discover_firefox_profiles(
                username,
                user_path,
                profile_inventory,
                statuses,
                errors
            )

            profile_counts[
                "Firefox"
            ] += len(
                firefox_profiles
            )

            for profile, profile_path in firefox_profiles:
                profile_work = os.path.join(
                    db_work,
                    safe_filename(
                        "{}_Firefox_{}".format(
                            username,
                            profile
                        )
                    )
                )

                firefox_history, firefox_downloads = (
                    collect_firefox_places(
                        username,
                        profile,
                        profile_path,
                        profile_work,
                        history_writer,
                        downloads_writer,
                        statuses,
                        errors
                    )
                )

                counts["history"] += (
                    firefox_history
                )

                counts["downloads"] += (
                    firefox_downloads
                )

                # Intentionally conservative:
                # Firefox search terms are not guessed from arbitrary URLs.
                add_status(
                    statuses,
                    username,
                    "Firefox",
                    profile,
                    "search_terms",
                    os.path.join(
                        profile_path,
                        "places.sqlite"
                    ),
                    "not_collected",
                    0,
                    "No stable explicit search-term table used by this collector"
                )

                counts["bookmarks"] += (
                    collect_firefox_bookmarks(
                        username,
                        profile,
                        profile_path,
                        profile_work,
                        bookmark_writer,
                        statuses,
                        errors
                    )
                )

                counts["sessions"] += (
                    collect_firefox_sessions(
                        username,
                        profile,
                        profile_path,
                        sessions_writer,
                        statuses,
                        errors
                    )
                )

    finally:
        history_handle.close()
        downloads_handle.close()
        search_handle.close()
        bookmark_handle.close()
        sessions_handle.close()

    # -------------------------------------------------------------------------
    # Remove copied browser databases from final package.
    # -------------------------------------------------------------------------

    try:
        shutil.rmtree(
            db_work
        )

    except Exception as exc:
        record_error(
            errors,
            "Workspace Cleanup",
            db_work,
            exc
        )

    # -------------------------------------------------------------------------
    # Diagnostics / inventory
    # -------------------------------------------------------------------------

    write_rows(
        os.path.join(
            work_dir,
            "profile_inventory.csv"
        ),
        inventory_fields,
        profile_inventory
    )

    write_rows(
        os.path.join(
            work_dir,
            "artifact_status.csv"
        ),
        status_fields,
        statuses
    )

    write_rows(
        os.path.join(
            work_dir,
            "errors.csv"
        ),
        error_fields,
        errors
    )

    total_profiles = sum(
        profile_counts.values()
    )

    total_records = sum(
        counts.values()
    )

    if total_profiles == 0:
        status = (
            "completed_no_browser_profiles"
        )

    elif total_records == 0:
        status = (
            "completed_no_records"
        )

    elif errors:
        status = (
            "completed_with_errors"
        )

    else:
        status = (
            "completed"
        )

    collection_end = utc_now()

    manifest = {
        "collector":
            COLLECTOR_NAME,

        "version":
            COLLECTOR_VERSION,

        "hostname":
            hostname,

        "status":
            status,

        "collection_start_utc":
            collection_start,

        "collection_end_utc":
            collection_end,

        "users_root":
            get_users_root(),

        "discovered_users":
            len(users),

        "discovered_profiles":
            total_profiles,

        "browser_profile_counts":
            profile_counts,

        "records":
            counts,

        "total_records":
            total_records,

        "artifact_status_entries":
            len(statuses),

        "errors":
            len(errors),

        "scope": [
            "history",
            "visits",
            "downloads",
            "search_terms_when_explicit",
            "visit_transitions",
            "referrer_chain_identifiers",
            "bookmarks",
            "session_metadata",
            "profile_metadata"
        ],

        "excluded_scope": [
            "cookies",
            "cookie_decryption",
            "passwords",
            "login_data",
            "autofill",
            "payment_data",
            "cache",
            "indexeddb",
            "local_storage"
        ]
    }

    with open(
        os.path.join(
            work_dir,
            "manifest.json"
        ),
        "w",
        encoding="utf-8"
    ) as handle:
        json.dump(
            manifest,
            handle,
            indent=2
        )

    # Human-friendly quick summary.
    summary_lines = [
        "Browser Triage Collector {}".format(
            COLLECTOR_VERSION
        ),
        "",
        "Hostname: {}".format(
            hostname
        ),
        "Status: {}".format(
            status
        ),
        "Users discovered: {}".format(
            len(users)
        ),
        "Profiles discovered: {}".format(
            total_profiles
        ),
        "",
        "Chrome profiles: {}".format(
            profile_counts["Chrome"]
        ),
        "Edge profiles: {}".format(
            profile_counts["Edge"]
        ),
        "Brave profiles: {}".format(
            profile_counts["Brave"]
        ),
        "Firefox profiles: {}".format(
            profile_counts["Firefox"]
        ),
        "",
        "History records: {}".format(
            counts["history"]
        ),
        "Download records: {}".format(
            counts["downloads"]
        ),
        "Search-term records: {}".format(
            counts["search_terms"]
        ),
        "Bookmark records: {}".format(
            counts["bookmarks"]
        ),
        "Session metadata records: {}".format(
            counts["sessions"]
        ),
        "Errors: {}".format(
            len(errors)
        ),
        "",
        "See artifact_status.csv for per-profile parsing status.",
        "See errors.csv for failures."
    ]

    with open(
        os.path.join(
            work_dir,
            "summary.txt"
        ),
        "w",
        encoding="utf-8"
    ) as handle:
        handle.write(
            "\n".join(
                summary_lines
            )
        )

    create_zip(
        work_dir,
        output_zip
    )

    try:
        shutil.rmtree(
            work_dir
        )

    except Exception:
        # ZIP is already complete.
        pass

    return {
        "status":
            status,

        "collector":
            COLLECTOR_NAME,

        "version":
            COLLECTOR_VERSION,

        "hostname":
            hostname,

        "discovered_users":
            len(users),

        "discovered_profiles":
            total_profiles,

        "chrome_profiles":
            profile_counts[
                "Chrome"
            ],

        "edge_profiles":
            profile_counts[
                "Edge"
            ],

        "brave_profiles":
            profile_counts[
                "Brave"
            ],

        "firefox_profiles":
            profile_counts[
                "Firefox"
            ],

        "history_records":
            counts[
                "history"
            ],

        "download_records":
            counts[
                "downloads"
            ],

        "search_term_records":
            counts[
                "search_terms"
            ],

        "bookmark_records":
            counts[
                "bookmarks"
            ],

        "session_records":
            counts[
                "sessions"
            ],

        "total_records":
            total_records,

        "errors":
            len(errors),

        "output_path":
            output_zip,

        "files_to_get": [
            output_zip
        ]
    }


# =============================================================================
# Optional cleanup Cortex entry point
# =============================================================================

def delete_output(path):
    """
    Delete exactly one collector-created ZIP from tempfile.gettempdir().
    """
    if not path:
        return {
            "status":
                "error",

            "message":
                "Path is empty"
        }

    try:
        temp_root = os.path.realpath(
            tempfile.gettempdir()
        )

        target_path = os.path.realpath(
            os.path.abspath(
                path
            )
        )

        target_dir = os.path.realpath(
            os.path.dirname(
                target_path
            )
        )

        if (
            os.path.normcase(
                target_dir
            )
            !=
            os.path.normcase(
                temp_root
            )
        ):
            return {
                "status":
                    "rejected",

                "message":
                    "File must be directly inside the temp directory"
            }

        filename = os.path.basename(
            target_path
        )

        if not filename.lower().startswith(
            OUTPUT_PREFIX.lower()
        ):
            return {
                "status":
                    "rejected",

                "message":
                    "Unexpected output filename"
            }

        if not filename.lower().endswith(
            ".zip"
        ):
            return {
                "status":
                    "rejected",

                "message":
                    "Only collector ZIP files are allowed"
            }

        if not os.path.isfile(
            target_path
        ):
            return {
                "status":
                    "not_found",

                "path":
                    target_path
            }

        os.remove(
            target_path
        )

        return {
            "status":
                "deleted",

            "path":
                target_path
        }

    except Exception as exc:
        return {
            "status":
                "error",

            "message":
                str(exc)
        }


# =============================================================================
# Local execution
# =============================================================================

if __name__ == "__main__":
    print(
        "[+] Browser Triage Collector {} starting...".format(
            COLLECTOR_VERSION
        )
    )

    result = main()

    print(
        "[+] Collection finished."
    )

    print(
        json.dumps(
            result,
            indent=2
        )
    )