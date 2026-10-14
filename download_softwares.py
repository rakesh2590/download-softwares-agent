from dotenv import load_dotenv
from claude_agent_sdk import query, ResultMessage, PermissionResultAllow, PermissionResultDeny
import asyncio
import csv
import re
import winreg
from pathlib import Path
from helper import base_options, parse_message


BASE_DIR = Path(__file__).parent
CSV_PATH = BASE_DIR / "requiredsoftwares.csv"
DOWNLOADS_DIR = BASE_DIR / "downloads"
MAX_BUDGET_PER_SOFTWARE_USD = 0.50

UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def read_softwares(csv_path: Path):
    softwares = []
    with open(csv_path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            softwares.append({
                "sno": (row.get("sno") or "").strip(),
                "name": name,
                "version": (row.get("version") or "").strip() or None,
                "url": (row.get("url") or "").strip() or None,
            })
    return softwares


def read_installed_programs():
    programs = []
    for hive, path in UNINSTALL_KEYS:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                        try:
                            version = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                        except OSError:
                            version = ""
                except OSError:
                    continue
                programs.append({"name": display_name, "version": version})
    return programs


def find_installed(name: str, programs: list[dict]):
    # Matches "Python" to "Python" or "Python 3.14.0 (64-bit)", but not "GitHub Desktop" for "Git"
    wanted = name.lower()
    matches = [
        program for program in programs
        if program["name"].lower() == wanted or program["name"].lower().startswith(wanted + " ")
    ]
    if not matches:
        return None
    return max(matches, key=lambda program: version_key(program["version"]))


def version_key(version: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", version or ""))


def is_older(installed: str, target: str) -> bool:
    a, b = version_key(installed), version_key(target)
    length = max(len(a), len(b))
    return a + (0,) * (length - len(a)) < b + (0,) * (length - len(b))


def build_prompt(software: dict) -> str:
    version = software["version"] or "latest stable"
    url = software["url"] or "not given, find it with WebSearch"
    return f"""Download the Windows 64-bit installer for this software.

Software: {software["name"]}
Version: {version}
Official page hint: {url}
Download folder: {DOWNLOADS_DIR}

Steps:
1. If no official page hint is given, use WebSearch to find the vendor's official download page. Do not use third-party download portals.
2. Use WebFetch to confirm the {version} version from the official page or release notes. Do not guess the version.
3. Find the direct download link for the Windows x64 installer of that version.
4. Download it into the download folder with PowerShell, for example:
   Invoke-WebRequest -Uri "<direct link>" -OutFile "{DOWNLOADS_DIR}\\<file name>"
5. Confirm the file exists and is not empty. Do NOT run or install the file.

End your reply with exactly one line in this format:
RESULT | <name> | <version downloaded> | <full file path> | <source url> | <OK or FAILED: reason>
"""


def build_install_prompt(name: str, version: str, file_path: str, existing_version: str | None) -> str:
    existing = (
        f"An older version ({existing_version}) is already installed. Install over it to upgrade."
        if existing_version else "No version is installed yet."
    )
    return f"""Install this downloaded Windows software silently.

Software: {name}
Version: {version}
Installer file: {file_path}
{existing}

Steps:
1. Check the installer's publisher with PowerShell: Get-AuthenticodeSignature "{file_path}". Report the publisher and status.
2. Find the silent install switches for this installer type (.msi uses msiexec, .exe uses its own silent switch). Use WebSearch or WebFetch on the vendor's docs if you are unsure.
3. Run the installer silently with PowerShell. Do NOT download anything new.
4. If possible, confirm the installed version.

End your reply with exactly one line in this format:
INSTALL | <name> | <OK or FAILED: reason>
"""


async def ask_human(tool_name, tool_input, context):
    # Read-only web lookups are allowed without asking. Every PowerShell command needs a human yes.
    if tool_name != "PowerShell":
        return PermissionResultAllow()
    print(f"\nAgent wants to run PowerShell:\n{tool_input.get('command', '')}")
    answer = input("Allow this command? (y/n): ").strip().lower()
    if answer == "y":
        return PermissionResultAllow()
    return PermissionResultDeny(message="Denied by user. Stop this step.", interrupt=True)


async def run_agent(prompt: str, options, fallback: str) -> str:
    result_text = ""
    async for message in query(prompt=prompt, options=options):
        parse_message(message)
        if isinstance(message, ResultMessage):
            result_text = message.result or ""

    for line in result_text.splitlines():
        if line.strip().startswith(("RESULT |", "INSTALL |")):
            return line.strip()
    return fallback


async def download_one(software: dict) -> str:
    options = base_options(
        system_prompt="You are a careful software download assistant. Never guess versions or URLs.",
        tools=["WebSearch", "WebFetch", "PowerShell"],
        permission_mode="default",
        can_use_tool=ask_human,
        cwd=str(DOWNLOADS_DIR),
        max_budget_usd=MAX_BUDGET_PER_SOFTWARE_USD,
    )
    return await run_agent(
        build_prompt(software),
        options,
        f"RESULT | {software['name']} | | | | FAILED: no result line returned",
    )


async def install_one(name: str, version: str, file_path: str, existing_version: str | None) -> str:
    options = base_options(
        system_prompt="You are a careful software install assistant. Only install the file you are given.",
        tools=["WebSearch", "WebFetch", "PowerShell"],
        permission_mode="default",
        can_use_tool=ask_human,
        cwd=str(DOWNLOADS_DIR),
        max_budget_usd=MAX_BUDGET_PER_SOFTWARE_USD,
    )
    return await run_agent(
        build_install_prompt(name, version, file_path, existing_version),
        options,
        f"INSTALL | {name} | FAILED: no result line returned",
    )


def parse_result(line: str) -> dict:
    parts = [part.strip() for part in line.split("|")[1:]]
    parts += [""] * (5 - len(parts))
    name, version, file_path, source_url, status = parts[:5]
    return {
        "name": name,
        "version": version,
        "file": file_path,
        "source_url": source_url,
        "status": status,
    }


async def download_softwares():
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    softwares = read_softwares(CSV_PATH)
    print(f"Found {len(softwares)} software(s) in {CSV_PATH.name}")
    programs = read_installed_programs()
    outcomes = {}

    downloads = []
    for software in softwares:
        print(f"\n===== {software['sno']}. {software['name']} =====")
        installed = find_installed(software["name"], programs)

        # Requested version already installed: skip the download entirely
        if installed and software["version"] and not is_older(installed["version"], software["version"]):
            print(f"Already installed: {installed['name']} {installed['version']}. Skipping download.")
            outcomes[software["name"]] = f"skipped, already installed ({installed['version']})"
            continue

        try:
            line = await download_one(software)
        except Exception as error:
            line = f"RESULT | {software['name']} | | | | FAILED: {error}"
        print(line)
        item = parse_result(line)
        item["installed"] = installed
        downloads.append(item)

    for item in downloads:
        name = item["name"]
        if not item["status"].startswith("OK") or not item["file"]:
            outcomes[name] = f"download failed ({item['status']})"
            continue

        installed = item["installed"]
        existing_version = installed["version"] if installed else None
        if installed is None:
            question = f"Install {name} {item['version']} from {item['file']}? (y/n): "
        elif not version_key(item["version"]):
            question = (f"{name} {installed['version']} is installed, but the downloaded version "
                        f"could not be read. Install over it anyway? (y/n): ")
        elif is_older(installed["version"], item["version"]):
            question = f"Upgrade {name} from {installed['version']} to {item['version']}? (y/n): "
        else:
            print(f"Already installed: {installed['name']} {installed['version']} "
                  f"(downloaded {item['version']}). Skipping install.")
            outcomes[name] = f"skipped, already installed ({installed['version']})"
            continue

        if input(f"\n{question}").strip().lower() != "y":
            print(f"Skipped install of {name}")
            outcomes[name] = "skipped by user"
            continue

        print(f"\n===== Installing {name} =====")
        try:
            line = await install_one(name, item["version"], item["file"], existing_version)
        except Exception as error:
            line = f"INSTALL | {name} | FAILED: {error}"
        print(line)
        outcomes[name] = line.split("|")[-1].strip()

    print("\n===== Summary =====")
    for name, outcome in outcomes.items():
        print(f"{name}: {outcome}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(download_softwares())
