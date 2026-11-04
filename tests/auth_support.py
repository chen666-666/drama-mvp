"""Synthetic website credentials and a private temporary session store for HTTP tests."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import auth


class AdminClient(TestClient):
    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.auth_patches = [patch.object(auth, "PRIVATE", Path(self.directory.name)),
            patch.object(auth, "config", return_value={"SITE_ORIGIN": "http://testserver", "ADMIN_USER": "synthetic-admin",
                         "ADMIN_PASSWORD_HASH": auth.password_hash("synthetic-password"), "SESSION_SECRET": "synthetic-secret-" * 4})]
        for item in self.auth_patches:
            item.start()
        super().__enter__()
        self.headers["Origin"] = "http://testserver"
        self.headers["X-CSRF-Token"] = self.get("/api/auth/session").json()["csrf_token"]
        result = self.post("/api/auth/login", json={"username": "synthetic-admin", "password": "synthetic-password"})
        assert result.status_code == 200, result.text
        self.headers["X-CSRF-Token"] = self.get("/api/auth/session").json()["csrf_token"]
        return self

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            for item in reversed(self.auth_patches):
                item.stop()
            self.directory.cleanup()
