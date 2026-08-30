import os
import re
import json
import csv
import zipfile
import socket
import tempfile
import datetime
import shutil


BROWSER_PATHS = {
    "Chrome": os.path.join(
        "AppData", "Local", "Google", "Chrome", "User Data"
    ),
    "Edge": os.path.join(
        "AppData", "Local", "Microsoft", "Edge", "User Data"
    ),
    "Brave": os.path.join(
        "AppData", "Local", "BraveSoftware", "Brave-Browser", "User Data"
    ),
    "Firefox": os.path.join(
        "AppData", "Roaming", "Mozilla", "Firefox", "Profiles"
    ),
}


SECURITY_RELEVANT_PERMISSIONS = {
    "debugger",
    "nativeMessaging",
    "history",
    "downloads",
    "cookies",
    "proxy",
    "management",
    "webRequest",
    "webRequestBlocking",
    "webRequestAuthProvider",
    "clipboardRead",
    "clipboardWrite",
    "geolocation",
}


BROAD_HOST_PATTERNS = {
    "<all_urls>",
    "*://*/*",
    "http://*/*",
    "https://*/*",
}


STATS = {
    "errors": 0,
    "skipped": 0,
}


def reset_stats():
    STATS["errors"] = 0
    STATS["skipped"] = 0


def record_error():
    STATS["errors"] += 1


def record_skip():
    STATS["skipped"] += 1


def discover_users():
    users_root = r"C:\Users"

    ignored_profiles = {
        "Public",
        "Default",
        "Default User",
        "All Users",
        "desktop.ini",
    }

    users = []

    if not os.path.isdir(users_root):
        record_error()
        return users

    try:
        entries = os.listdir(users_root)
    except OSError:
        record_error()
        return users

    for name in entries:

        if name in ignored_profiles:
            continue

        profile_path = os.path.join(
            users_root,
            name
        )

        try:
            if not os.path.isdir(profile_path):
                continue
        except OSError:
            record_skip()
            continue

        users.append({
            "username": name,
            "profile_path": profile_path
        })

    return users


def discover_browsers(users):
    browsers = []

    for user in users:

        for browser_name, relative_path in BROWSER_PATHS.items():

            browser_path = os.path.join(
                user["profile_path"],
                relative_path
            )

            try:
                if not os.path.isdir(browser_path):
                    continue
            except OSError:
                record_skip()
                continue

            browsers.append({
                "username": user["username"],
                "browser": browser_name,
                "browser_path": browser_path
            })

    return browsers


def discover_profiles(browsers):
    profiles = []

    for browser in browsers:

        browser_name = browser["browser"]
        browser_path = browser["browser_path"]

        try:
            entries = os.listdir(browser_path)
        except OSError:
            record_error()
            continue

        if browser_name in {
            "Chrome",
            "Edge",
            "Brave"
        }:

            for entry in entries:

                profile_path = os.path.join(
                    browser_path,
                    entry
                )

                try:
                    if not os.path.isdir(profile_path):
                        continue
                except OSError:
                    record_skip()
                    continue

                if (
                    entry == "Default"
                    or entry.startswith("Profile ")
                ):
                    profiles.append({
                        "username": browser["username"],
                        "browser": browser_name,
                        "profile": entry,
                        "profile_path": profile_path
                    })

        elif browser_name == "Firefox":

            for entry in entries:

                profile_path = os.path.join(
                    browser_path,
                    entry
                )

                try:
                    if not os.path.isdir(profile_path):
                        continue
                except OSError:
                    record_skip()
                    continue

                profiles.append({
                    "username": browser["username"],
                    "browser": browser_name,
                    "profile": entry,
                    "profile_path": profile_path
                })

    return profiles


def read_json(path):
    try:
        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except (
        OSError,
        ValueError,
        UnicodeDecodeError
    ):
        record_error()
        return None


def get_manifest_path(extension_path):
    try:
        entries = os.listdir(extension_path)
    except OSError:
        record_error()
        return None

    candidates = []

    for entry in entries:

        version_path = os.path.join(
            extension_path,
            entry
        )

        try:
            if not os.path.isdir(version_path):
                continue
        except OSError:
            record_skip()
            continue

        manifest_path = os.path.join(
            version_path,
            "manifest.json"
        )

        if os.path.isfile(manifest_path):
            candidates.append(manifest_path)

    if not candidates:
        record_skip()
        return None

    try:
        candidates.sort(
            key=os.path.getmtime,
            reverse=True
        )
    except OSError:
        record_error()

    return candidates[0]


def resolve_localized_name(
    name,
    manifest,
    manifest_path
):

    if not isinstance(name, str):
        return "Unknown"

    match = re.fullmatch(
        r"__MSG_(.+)__",
        name,
        re.IGNORECASE
    )

    if not match:
        return name

    message_key = match.group(1)

    version_path = os.path.dirname(
        manifest_path
    )

    locales_path = os.path.join(
        version_path,
        "_locales"
    )

    if not os.path.isdir(locales_path):
        return name

    default_locale = manifest.get(
        "default_locale"
    )

    locales_to_try = []

    if default_locale:
        locales_to_try.append(
            default_locale
        )

    for fallback in (
        "en",
        "en_US",
        "en_GB"
    ):

        if fallback not in locales_to_try:
            locales_to_try.append(
                fallback
            )

    try:
        available = os.listdir(locales_path)
    except OSError:
        available = []

    for locale_name in available:

        if locale_name not in locales_to_try:
            locales_to_try.append(
                locale_name
            )

    for locale_name in locales_to_try:

        messages_path = os.path.join(
            locales_path,
            locale_name,
            "messages.json"
        )

        if not os.path.isfile(messages_path):
            continue

        messages = read_json(
            messages_path
        )

        if not isinstance(messages, dict):
            continue

        for key, value in messages.items():

            if key.lower() != message_key.lower():
                continue

            if not isinstance(value, dict):
                continue

            resolved = value.get(
                "message"
            )

            if resolved:
                return resolved

    return name


def is_host_permission(permission):

    if not isinstance(permission, str):
        return False

    if permission == "<all_urls>":
        return True

    if "://" in permission:
        return True

    return False


def normalize_permissions(manifest):

    raw_permissions = manifest.get(
        "permissions",
        []
    )

    raw_host_permissions = manifest.get(
        "host_permissions",
        []
    )

    if not isinstance(raw_permissions, list):
        raw_permissions = []

    if not isinstance(raw_host_permissions, list):
        raw_host_permissions = []

    permissions = []
    host_permissions = []

    for permission in raw_permissions:

        if is_host_permission(permission):
            host_permissions.append(permission)
        else:
            permissions.append(permission)

    for permission in raw_host_permissions:

        if isinstance(permission, str):
            host_permissions.append(permission)

    permissions = list(
        dict.fromkeys(permissions)
    )

    host_permissions = list(
        dict.fromkeys(host_permissions)
    )

    return permissions, host_permissions


def get_security_relevant_permissions(
    permissions,
    host_permissions
):

    findings = []

    for permission in permissions:

        if permission in SECURITY_RELEVANT_PERMISSIONS:
            findings.append(permission)

    for host_permission in host_permissions:

        if host_permission in BROAD_HOST_PATTERNS:

            findings.append(
                "broad_host_access"
            )

            break

    return list(
        dict.fromkeys(findings)
    )


def collect_chromium_extensions(profile):

    extensions = []

    extensions_path = os.path.join(
        profile["profile_path"],
        "Extensions"
    )

    if not os.path.isdir(extensions_path):
        return extensions

    try:
        extension_ids = os.listdir(
            extensions_path
        )
    except OSError:
        record_error()
        return extensions

    for extension_id in extension_ids:

        if not re.fullmatch(
            r"[a-p]{32}",
            extension_id
        ):
            continue

        extension_path = os.path.join(
            extensions_path,
            extension_id
        )

        if not os.path.isdir(extension_path):
            continue

        manifest_path = get_manifest_path(
            extension_path
        )

        if not manifest_path:
            continue

        manifest = read_json(
            manifest_path
        )

        if not isinstance(manifest, dict):
            continue

        raw_name = manifest.get(
            "name",
            "Unknown"
        )

        name = resolve_localized_name(
            raw_name,
            manifest,
            manifest_path
        )

        (
            permissions,
            host_permissions
        ) = normalize_permissions(
            manifest
        )

        security_relevant = (
            get_security_relevant_permissions(
                permissions,
                host_permissions
            )
        )

        extensions.append({
            "username": profile["username"],
            "browser": profile["browser"],
            "profile": profile["profile"],
            "name": name,
            "extension_id": extension_id,
            "version": manifest.get(
                "version",
                "Unknown"
            ),
            "manifest_version": manifest.get(
                "manifest_version",
                "Unknown"
            ),
            "permissions": permissions,
            "host_permissions": host_permissions,
            "security_relevant_permissions": security_relevant,
            "extension_path": extension_path
        })

    return extensions


def collect_firefox_extensions(profile):

    extensions = []

    extensions_json = os.path.join(
        profile["profile_path"],
        "extensions.json"
    )

    if not os.path.isfile(extensions_json):
        return extensions

    data = read_json(
        extensions_json
    )

    if not isinstance(data, dict):
        return extensions

    addons = data.get(
        "addons",
        []
    )

    if not isinstance(addons, list):
        return extensions

    for addon in addons:

        if not isinstance(addon, dict):
            continue

        if addon.get("type") != "extension":
            continue

        extension_id = addon.get(
            "id",
            "Unknown"
        )

        locale_data = addon.get(
            "defaultLocale",
            {}
        )

        if isinstance(locale_data, dict):

            name = locale_data.get(
                "name",
                addon.get(
                    "name",
                    "Unknown"
                )
            )

        else:

            name = addon.get(
                "name",
                "Unknown"
            )

        version = addon.get(
            "version",
            "Unknown"
        )

        extension_path = addon.get(
            "path",
            ""
        )

        user_permissions = addon.get(
            "userPermissions",
            {}
        )

        permissions = []
        host_permissions = []

        if isinstance(user_permissions, dict):

            permissions = user_permissions.get(
                "permissions",
                []
            )

            host_permissions = user_permissions.get(
                "origins",
                []
            )

        if not isinstance(permissions, list):
            permissions = []

        if not isinstance(host_permissions, list):
            host_permissions = []

        security_relevant = (
            get_security_relevant_permissions(
                permissions,
                host_permissions
            )
        )

        extensions.append({
            "username": profile["username"],
            "browser": "Firefox",
            "profile": profile["profile"],
            "name": name,
            "extension_id": extension_id,
            "version": version,
            "manifest_version": "N/A",
            "permissions": permissions,
            "host_permissions": host_permissions,
            "security_relevant_permissions": security_relevant,
            "extension_path": extension_path
        })

    return extensions


def discover_extensions(profiles):

    extensions = []

    for profile in profiles:

        try:

            if profile["browser"] == "Firefox":

                extensions.extend(
                    collect_firefox_extensions(
                        profile
                    )
                )

            else:

                extensions.extend(
                    collect_chromium_extensions(
                        profile
                    )
                )

        except Exception:
            record_error()
            continue

    return extensions


def write_csv(
    extensions,
    csv_path
):

    fieldnames = [
        "hostname",
        "username",
        "browser",
        "profile",
        "name",
        "extension_id",
        "version",
        "manifest_version",
        "permissions",
        "host_permissions",
        "security_relevant_permissions",
        "extension_path",
    ]

    hostname = socket.gethostname()

    with open(
        csv_path,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for extension in extensions:

            writer.writerow({
                "hostname": hostname,
                "username": extension[
                    "username"
                ],
                "browser": extension[
                    "browser"
                ],
                "profile": extension[
                    "profile"
                ],
                "name": extension[
                    "name"
                ],
                "extension_id": extension[
                    "extension_id"
                ],
                "version": extension[
                    "version"
                ],
                "manifest_version": extension[
                    "manifest_version"
                ],
                "permissions": "; ".join(
                    extension[
                        "permissions"
                    ]
                ),
                "host_permissions": "; ".join(
                    extension[
                        "host_permissions"
                    ]
                ),
                "security_relevant_permissions": "; ".join(
                    extension[
                        "security_relevant_permissions"
                    ]
                ),
                "extension_path": extension[
                    "extension_path"
                ],
            })


def create_output_zip(extensions):

    hostname = socket.gethostname()

    timestamp = (
        datetime.datetime.now()
        .strftime("%Y%m%d_%H%M%S")
    )

    temp_root = tempfile.gettempdir()

    working_dir = tempfile.mkdtemp(
        prefix="browser_extensions_work_"
    )

    csv_name = (
        "browser_extensions_"
        + hostname
        + "_"
        + timestamp
        + ".csv"
    )

    zip_name = (
        "browser_extensions_"
        + hostname
        + "_"
        + timestamp
        + ".zip"
    )

    csv_path = os.path.join(
        working_dir,
        csv_name
    )

    zip_path = os.path.join(
        temp_root,
        zip_name
    )

    try:

        write_csv(
            extensions,
            csv_path
        )

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED
        ) as archive:

            archive.write(
                csv_path,
                arcname=csv_name
            )

    finally:

        shutil.rmtree(
            working_dir,
            ignore_errors=True
        )

    return zip_path


def main():
    """
    Cortex XDR collection entry point.
    """

    reset_stats()

    users = discover_users()

    browsers = discover_browsers(
        users
    )

    profiles = discover_profiles(
        browsers
    )

    extensions = discover_extensions(
        profiles
    )

    output_path = create_output_zip(
        extensions
    )

    flagged = sum(
        1
        for extension in extensions
        if extension[
            "security_relevant_permissions"
        ]
    )

    return {
        "hostname": socket.gethostname(),
        "users_scanned": len(users),
        "browsers_found": len(browsers),
        "profiles_scanned": len(profiles),
        "extensions_found": len(extensions),
        "extensions_with_security_relevant_permissions": flagged,
        "skipped_items": STATS["skipped"],
        "errors": STATS["errors"],
        "output_path": output_path,
        "files_to_get": [
            output_path
        ]
    }


# Local testing only.
# Cortex XDR will invoke main() directly through the entry point.
if __name__ == "__main__":

    result = main()

    print(
        json.dumps(
            result,
            indent=2
        )
    )