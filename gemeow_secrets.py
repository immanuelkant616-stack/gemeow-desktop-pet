"""Optional local API-key storage using Windows user-scoped DPAPI.

The caller decides whether to remember a key. No key is written by constructing
this object or by calling ``load``. The on-disk file contains only a version
header and the DPAPI-protected bytes, never a plaintext key.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile


_HEADER = b"GEMEOW-DPAPI\x01"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class SecretStore:
    """Remember one API key for the current Windows user on this computer."""

    def __init__(self, data_dir: Path):
        self.available = os.name == "nt"
        self._path = Path(data_dir) / "api_key.dpapi"

    def load(self) -> str:
        """Return the saved key, or an empty string if no key has been saved.

        A damaged or inaccessible file is preserved so the caller can decide
        whether to delete it, instead of silently replacing the saved key.
        """
        try:
            protected = self._path.read_bytes()
        except FileNotFoundError:
            return ""
        except OSError as exc:
            raise RuntimeError("저장된 API 키 파일을 읽지 못했습니다.") from exc

        self._require_available()
        if not protected.startswith(_HEADER) or len(protected) <= len(_HEADER):
            raise RuntimeError("저장된 API 키 파일이 손상되었습니다. 설정에서 키를 다시 저장해 주세요.")
        try:
            key = self._crypt(protected[len(_HEADER):], protect=False).decode("utf-8")
        except (RuntimeError, UnicodeError) as exc:
            raise RuntimeError(
                "저장된 API 키를 복호화하지 못했습니다. "
                "키를 저장한 Windows 계정으로 실행하거나 설정에서 키를 다시 입력해 주세요."
            ) from exc
        if not key.strip():
            raise RuntimeError("저장된 API 키가 비어 있습니다. 설정에서 키를 다시 입력해 주세요.")
        return key

    def save(self, api_key: str) -> None:
        """Encrypt a key first, then atomically replace the previous file."""
        self._require_available()
        if not isinstance(api_key, str):
            raise TypeError("API 키는 문자열이어야 합니다.")
        if not api_key.strip():
            raise ValueError("API 키가 비어 있습니다.")
        protected = _HEADER + self._crypt(api_key.encode("utf-8"), protect=True)
        temp_path: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".api-key-", suffix=".tmp", dir=self._path.parent)
            temp_path = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self._path)
        except OSError as exc:
            raise RuntimeError("API 키를 안전하게 저장하지 못했습니다. 폴더의 쓰기 권한을 확인해 주세요.") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass  # The temporary file contains ciphertext only.

    def clear(self) -> None:
        """Delete the saved encrypted key; succeed if it is already absent."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError("저장된 API 키를 삭제하지 못했습니다. 파일 권한을 확인해 주세요.") from exc

    def _require_available(self) -> None:
        if not self.available:
            raise RuntimeError("API 키의 안전한 저장 기능은 Windows에서만 사용할 수 있습니다.")

    @staticmethod
    def _crypt(data: bytes, *, protect: bool) -> bytes:
        # Windows DPAPI binds ciphertext to the current user by default. Do not
        # use CRYPTPROTECT_LOCAL_MACHINE: another account must not decrypt it.
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_ptr = ctypes.POINTER(_DataBlob)
        crypt32.CryptProtectData.argtypes = [
            blob_ptr, wintypes.LPCWSTR, blob_ptr, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, blob_ptr,
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            blob_ptr, ctypes.POINTER(wintypes.LPWSTR), blob_ptr, ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, blob_ptr,
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        # Keep the input buffer alive until the native call has returned.
        input_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        input_blob = _DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output_blob = _DataBlob()
        try:
            if protect:
                succeeded = crypt32.CryptProtectData(
                    ctypes.byref(input_blob), "Gemeow API key", None, None,
                    None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob),
                )
            else:
                succeeded = crypt32.CryptUnprotectData(
                    ctypes.byref(input_blob), None, None, None,
                    None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob),
                )
            if not succeeded:
                error_code = ctypes.get_last_error()
                raise RuntimeError(f"Windows 키 보호 처리에 실패했습니다. (오류 {error_code})")
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
