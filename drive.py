import io
import logging
from datetime import date
from typing import Dict

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import config

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveClient:
    def __init__(self) -> None:
        self._service = None
        self._folder_cache: Dict[str, str] = {}

    @property
    def service(self):
        if self._service is None:
            creds = service_account.Credentials.from_service_account_file(
                config.GOOGLE_CREDENTIALS_FILE, scopes=_SCOPES
            )
            self._service = build("drive", "v3", credentials=creds)
        return self._service

    def _get_or_create_folder(self, name: str, parent_id: str) -> str:
        cache_key = f"{parent_id}/{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        query = (
            f"name='{name}' "
            f"and mimeType='application/vnd.google-apps.folder' "
            f"and '{parent_id}' in parents "
            f"and trashed=false"
        )
        result = self.service.files().list(
            q=query, fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = result.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            metadata = {
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            folder = self.service.files().create(
                body=metadata, fields="id", supportsAllDrives=True
            ).execute()
            folder_id = folder["id"]
            logger.info("Created Drive folder: %s (id=%s)", name, folder_id)

        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _get_monthly_folder(self, booking_date: date) -> str:
        receipts_id = self._get_or_create_folder("Receipts", config.DRIVE_FOLDER_ID)
        month_label = booking_date.strftime("%b-%Y")
        return self._get_or_create_folder(month_label, receipts_id)

    def upload_receipt(
        self,
        file_bytes: bytes,
        booking_number: str,
        booking_date: date,
        mime_type: str = "image/jpeg",
    ) -> str:
        filename = f"booking_{booking_number}_receipt_{booking_date.strftime('%Y-%m-%d')}"
        folder_id = self._get_monthly_folder(booking_date)

        media = MediaIoBaseUpload(
            io.BytesIO(file_bytes), mimetype=mime_type, resumable=False
        )
        file_meta = {"name": filename, "parents": [folder_id]}

        uploaded = self.service.files().create(
            body=file_meta, media_body=media, fields="id", supportsAllDrives=True
        ).execute()
        file_id = uploaded["id"]

        self.service.files().permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()

        info = self.service.files().get(
            fileId=file_id, fields="webViewLink", supportsAllDrives=True
        ).execute()

        link = info.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
        logger.info("Uploaded receipt: %s → %s", filename, link)
        return link


drive_client = DriveClient()
