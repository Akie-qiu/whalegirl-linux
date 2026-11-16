"""Static asset/authentication regression for the new shell; no external services."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from whalegirl_linux.app import create_app


ROOT = Path(__file__).resolve().parents[1]


class Gateway:
    login = {}

    async def close(self):
        pass


class StudioConsoleTests(unittest.TestCase):
    def test_new_shell_assets_keep_api_and_host_boundary(self):
        parent = ROOT / "artifacts/studio-ui-20261110"
        parent.mkdir(parents=True, exist_ok=True)
        data = Path(tempfile.mkdtemp(prefix="routes-", dir=parent))
        app = create_app(data, gateway=Gateway(), run_background=False)
        with TestClient(app) as client:
            for path, mime in [("/studio.js", "text/javascript"),
                               ("/studio.css", "text/css"),
                               ("/studio-background.jpg", "image/jpeg")]:
                with self.subTest(path=path):
                    result = client.get(path)
                    self.assertEqual(result.status_code, 200)
                    self.assertIn(mime, result.headers["content-type"])
                    self.assertEqual(result.headers["x-content-type-options"], "nosniff")
                    self.assertGreater(len(result.content), 1000)
            html = client.get("/").text
            self.assertIn('/studio.js?v=', html)
            self.assertIn('/studio.css?v=', html)
            self.assertEqual(client.get("/api/config").status_code, 401)
            self.assertEqual(client.get("/studio.js", headers={"Host": "untrusted.example"}).status_code, 403)
            self.assertEqual(client.get("/assets/admin.token").status_code, 404)
            token = (data / "admin.token").read_text().strip()
            headers = {"Authorization": "Bearer " + token}
            self.assertEqual(client.get("/api/config", headers=headers).status_code, 200)
            self.assertEqual(client.get("/health").json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
