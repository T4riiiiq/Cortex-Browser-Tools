# Cortex Browser Tools

Two Windows browser collection scripts designed for DFIR and incident-response investigations. They can be executed locally or through **Cortex XDR Endpoint Scripts**.

## Included Scripts

- `browser_History.py` collects browser history, downloads, stored search terms, bookmarks, and session metadata from Chrome, Edge, Brave, and Firefox.
- `browser_extensions.py` inventories installed browser extensions, their versions, locations, and permissions. It also highlights security-relevant permissions.

> These scripts do not collect passwords, payment information, or decrypted cookies. They use Python standard-library modules only.

## Run Locally

Requirements:

- Windows
- Python 3.7 or newer
- Administrator privileges are recommended when scanning profiles belonging to multiple users.

Clone the repository and open PowerShell inside it:

```powershell
git clone https://github.com/T4riiiiq/Cortex-Browser-Tools.git
cd Cortex-Browser-Tools
```

Collect browser activity:

```powershell
python .\browser_History.py
```

Collect installed browser extensions:

```powershell
python .\browser_extensions.py
```

When a script finishes, it prints a JSON result containing `output_path`. This is the location of the generated ZIP file, which is normally stored in the Windows temporary directory (`%TEMP%`).

## Run Through Cortex XDR

Repeat these steps for each script:

1. Open **Cortex XDR → Action Center → Scripts Library → New Script**.
2. Upload the required Python file.
3. Select **Windows** as the platform.
4. Select **Run by entry point** and use `main` as the entry point.
5. Do not add any parameters.
6. Select **Dictionary** as the output type.
7. Start with a timeout of `900` seconds.
8. Run the script on one test endpoint first.
9. Open the execution result and download the ZIP listed under `files_to_get`.

## Review Browser-History Results

Start with these files inside the ZIP produced by `browser_History.py`:

- `summary.txt` — quick collection summary and record counts.
- `profile_inventory.csv` — discovered browsers and profiles.
- `artifact_status.csv` — collection and parsing status for each artifact.
- `errors.csv` — details of any collection failures.

Investigation data is stored in files such as:

- `history.csv`
- `downloads.csv`
- `search_terms.csv`
- `bookmarks.csv`
- `sessions.csv`

## Review Browser-Extension Results

The ZIP produced by `browser_extensions.py` contains a CSV inventory of installed extensions. Review extensions with powerful permissions such as:

- `debugger`
- `nativeMessaging`
- `proxy`
- `cookies`
- `webRequest`
- `<all_urls>`

A flagged permission does not automatically mean that an extension is malicious. It indicates that the extension deserves additional review.

## Recommended First Test

Run each script on one endpoint before deploying it widely. Confirm that:

1. Cortex successfully retrieves the ZIP file.
2. The CSV files contain real data.
3. `artifact_status.csv` does not report unexpected `copy_failed` or `parse_failed` entries.
4. The scripts also work while the browsers are open.

After a successful test, expand deployment gradually to a small group of endpoints.

