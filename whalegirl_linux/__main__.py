import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("whalegirl_linux.app:create_app", factory=True, host="127.0.0.1",
                port=int(os.environ.get("WHALEGIRL_PORT", "8092")), access_log=False,
                limit_concurrency=32, backlog=128, timeout_keep_alive=5,
                timeout_graceful_shutdown=30)
