"""Explicit local maintenance CLI. Does not launch the application or migrate data."""

import argparse
import json
import asyncio
import getpass
from pathlib import Path

from services.agent_api.app.backup_bundle import create_bundle, restore_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("create")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--windows-user-encryption", action="store_true", help="Use Windows-user/machine-bound DPAPI. Without this flag the bundle is unencrypted; credentials/workspaces are excluded in both modes.")
    backup.add_argument('--portable-password', action='store_true', help='Prompt privately for a portable recovery passphrase; never pass it in command arguments.')
    restore = commands.add_parser("restore")
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument("--new-directory", type=Path, required=True)
    restore.add_argument('--portable-password', action='store_true')
    upload = commands.add_parser('upload')
    upload.add_argument('--bundle', type=Path, required=True)
    upload.add_argument('--endpoint', required=True)
    upload.add_argument('--expected-sha256', required=True)
    upload.add_argument('--credential-directory', type=Path, required=True)
    upload.add_argument('--credential-reference', required=True)
    upload.add_argument('--allow-storage-charges', action='store_true')
    download = commands.add_parser('download')
    download.add_argument('--endpoint', required=True)
    download.add_argument('--new-directory', type=Path, required=True)
    download.add_argument('--expected-sha256', required=True)
    download.add_argument('--credential-directory', type=Path, required=True)
    download.add_argument('--credential-reference', required=True)
    download.add_argument('--allow-storage-charges', action='store_true')
    arguments = parser.parse_args()
    if arguments.command == "create":
        password = getpass.getpass('Portable recovery passphrase: ') if arguments.portable_password else None
        if password is not None and password != getpass.getpass('Confirm recovery passphrase: '):
            raise SystemExit('Recovery passphrases did not match; no backup was created.')
        result = create_bundle(arguments.database, arguments.output, encrypt=arguments.windows_user_encryption, passphrase=password)
    elif arguments.command == 'restore':
        password = getpass.getpass('Recovery passphrase: ') if arguments.portable_password else None
        result = restore_bundle(arguments.bundle, arguments.new_directory, passphrase=password)
    elif arguments.command == 'upload':
        from services.agent_api.app.credentials import CredentialVault
        from services.agent_api.app.portable_backup import upload_encrypted_bundle
        credential = CredentialVault(arguments.credential_directory).resolve(arguments.credential_reference)
        password = getpass.getpass('Recovery passphrase to authenticate the encrypted bundle: ')
        result = asyncio.run(upload_encrypted_bundle(arguments.bundle, arguments.endpoint, passphrase=password,
            expected_sha256=arguments.expected_sha256, credential=credential,
            allow_storage_charges=arguments.allow_storage_charges, authorize=lambda: None))
    else:
        from services.agent_api.app.credentials import CredentialVault
        from services.agent_api.app.portable_backup import download_encrypted_bundle
        credential = CredentialVault(arguments.credential_directory).resolve(arguments.credential_reference)
        password = getpass.getpass('Recovery passphrase to authenticate the downloaded bundle: ')
        result = asyncio.run(download_encrypted_bundle(arguments.endpoint, arguments.new_directory,
            passphrase=password, expected_sha256=arguments.expected_sha256, credential=credential,
            allow_storage_charges=arguments.allow_storage_charges, authorize=lambda: None))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
