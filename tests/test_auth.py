"""Authentication boundaries on synthetic credentials and temporary session storage."""
import json
import sqlite3
import time
import unittest
from unittest.mock import patch

import auth
import main
from tests.auth_support import AdminClient


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(main, "init_db"), patch.object(main, "recover_jobs")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_password_salts_and_verification(self):
        first, second = auth.password_hash("example-password"), auth.password_hash("example-password")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.password_matches("example-password", first))
        self.assertFalse(auth.password_matches("wrong", first))

    def test_all_management_mutations_origin_and_csrf(self):
        with AdminClient(main.app) as client:
            for path in ["/api/runs", "/api/reviews", "/api/series/merge", "/api/series/1/metadata",
                         "/api/episodes/1/assign", "/api/runs/1/rankings", "/api/source/douk/session",
                         "/api/downloads/catalogs/1/download", "/api/downloads/rankings/1/initialize"]:
                self.assertEqual(client.post(path, json={}, headers={"Origin": "https://evil.example"}).status_code, 403, path)
                self.assertEqual(client.post(path, json={}, headers={"X-CSRF-Token": "invalid"}).status_code, 403, path)
            self.assertEqual(client.post("/api/runs", json={}, headers={"Host": "evil.example"}).status_code, 403)
            saved = client.cookies.get(auth.COOKIE)
            client.cookies.clear()
            for path in ["/api/downloads/status", "/api/downloads/rankings/1", "/api/downloads/media/1/file",
                         "/api/downloads/media/1/download", "/api/downloads/exports/1/file", "/api/source/douk/status"]:
                self.assertEqual(client.get(path).status_code, 401, path)
            self.assertEqual(client.post("/api/runs", json={}).status_code, 401)
            response = client.get("/downloads?ranking_id=42", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertIn("ranking_id%3D42", response.headers["location"])
            response = client.get("/downloads/watch?ranking_id=42&series_id=1", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertIn("%2Fdownloads%2Fwatch", response.headers["location"])
            with auth.store() as db:
                self.assertNotIn(saved, json.dumps(db.execute("SELECT * FROM sessions").fetchall()))

    def test_logout_expiry_and_missing_config(self):
        with AdminClient(main.app) as client:
            token = client.cookies.get(auth.COOKIE)
            self.assertEqual(client.post("/api/auth/logout").status_code, 200)
            client.cookies.set(auth.COOKIE, token)
            self.assertFalse(client.get("/api/auth/session").json()["authenticated"])
            cfg = auth.config()
            with auth.store() as db:
                db.execute("INSERT INTO sessions VALUES (?,?)", (auth.sign(token, cfg), time.time()-1))
            self.assertEqual(client.get("/api/downloads/status").status_code, 401)
            with patch.object(auth, "config", return_value={key: "" for key in cfg}):
                self.assertEqual(client.get("/api/downloads/status").status_code, 503)
                self.assertEqual(client.get("/health").status_code, 200)

    def test_login_csrf_and_rate_limit(self):
        with AdminClient(main.app) as client:
            client.cookies.clear()
            self.assertEqual(client.post("/api/auth/login", json={}).status_code, 403)
            csrf = client.get("/api/auth/session").json()["csrf_token"]
            for _ in range(5):
                result = client.post("/api/auth/login", json={"username": "synthetic-admin", "password": "wrong"}, headers={"X-CSRF-Token": csrf})
                self.assertEqual(result.status_code, 401)
                self.assertNotIn("wrong", result.text)
            self.assertEqual(client.post("/api/auth/login", json={}, headers={"X-CSRF-Token": csrf}).status_code, 429)

    def test_https_cookie_flags(self):
        with AdminClient(main.app) as client:
            cfg = {**auth.config(), "SITE_ORIGIN": "https://testserver"}
            with patch.object(auth, "config", return_value=cfg):
                client.cookies.clear()
                result = client.get("https://testserver/api/auth/session")
                header = result.headers["set-cookie"].lower()
                for flag in ("secure", "httponly", "samesite=lax"):
                    self.assertIn(flag, header)
                csrf = result.json()["csrf_token"]
                result = client.post("https://testserver/api/auth/login", json={"username": "synthetic-admin", "password": "synthetic-password"}, headers={"Origin": "https://testserver", "X-CSRF-Token": csrf})
                self.assertEqual(result.status_code, 200)
                self.assertIn("max-age=28800", result.headers["set-cookie"].lower())
