import base64
import os
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from payday.core.config import settings


class EncryptionService:
    """Provides AES-256-GCM encryption/decryption for sensitive data at rest."""
    def __init__(self, key_secret: str = settings.ENCRYPTION_KEY):
        # Derive a 256-bit (32 bytes) key using SHA-256
        self.key = hashlib.sha256(key_secret.encode("utf-8")).digest()
        self.aesgcm = AESGCM(self.key)

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypts a plaintext string into bytes: 12-byte nonce + ciphertext + tag."""
        if not plaintext:
            return b""
        nonce = os.urandom(12)  # 96-bit nonce for AES-GCM
        ciphertext = self.aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ciphertext

    def decrypt(self, encrypted_data: bytes) -> str:
        """Decrypts bytes into the original plaintext string."""
        if not encrypted_data or len(encrypted_data) < 12:
            return ""
        nonce = encrypted_data[:12]
        ciphertext = encrypted_data[12:]
        decrypted_bytes = self.aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted_bytes.decode("utf-8")

    def encrypt_to_base64(self, plaintext: str) -> str:
        encrypted_bytes = self.encrypt(plaintext)
        return base64.b64encode(encrypted_bytes).decode("utf-8")

    def decrypt_from_base64(self, b64_ciphertext: str) -> str:
        encrypted_bytes = base64.b64decode(b64_ciphertext.encode("utf-8"))
        return self.decrypt(encrypted_bytes)


encryption_service = EncryptionService()
