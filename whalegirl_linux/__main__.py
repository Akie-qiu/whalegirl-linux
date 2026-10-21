import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("whalegirl_linux.app:create_app", factory=True, host="127.0.0.1",
                port=int(os.environ.get("WHALEGIRL_PORT", "8092")), access_log=False)
