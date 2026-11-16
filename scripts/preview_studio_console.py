"""Local studio preview with synthetic records; never contacts models or WeChat."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.responses import JSONResponse, Response
import uvicorn
from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings

PREVIEW_TOKEN = "studio-local-preview"
ACCOUNT = "studio-preview-account"
CHATS = [
    {"id": "studio-friends@chatroom", "name": "海边的小客厅（示例）", "isGroup": True},
    {"id": "studio-test@chatroom", "name": "临时测试（示例）", "isGroup": True},
    {"id": "studio-person", "name": "工作室管理员（示例）", "isGroup": False},
]


class PreviewGateway:
    login = {}

    async def status(self, **kwargs):
        return {"status": "logged_in", "loggedInUser": ACCOUNT}

    async def chats(self):
        return CHATS

    async def messages(self, *args, **kwargs):
        return []

    async def close(self):
        pass


def forbidden_engine(**kwargs):
    raise RuntimeError("Local UI preview cannot call a model")


def preview_app(data):
    (data / "admin.token").write_text(PREVIEW_TOKEN, encoding="utf-8")
    app = create_app(data, gateway=PreviewGateway(), engine_factory=forbidden_engine,
                     run_background=False)
    app.state.config.save(Settings(enabled=True, account_id=ACCOUNT,
        allowed_chat_ids=[chat["id"] for chat in CHATS], api_style="openai",
        api_url="https://chat.example/v1", model="deepseek-preview",
        model_temperature=1.2, expected_wechat_version="4.1.1.8"), "synthetic-key-local-only")
    runtime = app.state.runtime
    runtime.gateway_state = {"status": "logged_in", "loggedInUser": ACCOUNT}
    runtime.account_id = ACCOUNT
    # Seed only this temporary, isolated database. These are labelled UI fixtures,
    # not model responses, WeChat receipts or production conversations.
    fixtures = [
        (CHATS[0]["id"], "小夏（示例）", "演示消息：我刚刚发的那个视频太离谱了", "演示回复：等等，让我看看。", "sent"),
        (CHATS[2]["id"], "管理员（示例）", "演示消息：最近在听什么歌？", "演示回复：今天想听点轻快的，你有推荐吗", "sent"),
        (CHATS[0]["id"], "阿青（示例）", "演示消息：晚上八点再约", "", "ignored"),
    ]
    for index, (chat, sender, content, reply, fixture_state) in enumerate(fixtures):
        event = {"key": f"harbor-fixture-{index}", "account": ACCOUNT,
                 "chat": chat, "sender": sender, "sender_id": f"preview-person-{index}",
                 "content": content, "timestamp": time.time() - (index + 1) * 180,
                 "local_id": index + 1, "kind": "text", "is_self": False}
        runtime.store.observe([event])
        runtime.store.insert(event, state=fixture_state)
        runtime.store.update(event["key"], fixture_state, reply=reply,
                             receipt="synthetic-preview-only",
                             error="合成示例：没有触发唤醒" if fixture_state == "ignored" else "")
    examples = [
        "喜欢下雨时听轻音乐", "对海洋纪录片感兴趣", "约好周末聊新发现的电影",
        "喜欢简短自然的问候", "最近在找适合下午听的歌", "喜欢猫咪的日常视频",
        "一起讨论过做饭的小技巧", "喜欢晴天去散步", "想试试画一只小鲸鱼",
        "记得朋友推荐过一本游记", "更喜欢轻松的聊天节奏", "最近关注城市里的花",
        "想了解一点摄影构图", "喜欢分享生活里的小发现", "聊过一次有趣的展览",
        "喜欢草莓口味的小点心", "朋友提过窗边新长出的嫩芽", "想整理一份周末歌单",
    ]
    for index, content in enumerate(examples):
        runtime.memory_book.change(ACCOUNT, app.state.config.settings.allowed_chat_ids,
            {"op": "create", "chat": CHATS[0]["id"], "subject": "studio-person",
             "kind": "preference", "content": "合成预览资料：" + content,
             "source_key": "studio-fixture-" + str(index)})
    account = runtime.bilibili.snapshot()["account"]
    from preview_usage_fixtures import seed_usage
    seed_usage(runtime,CHATS)
    counters = {"polls": 0}
    # Mutating external tools are blocked even if a visitor clicks their buttons.
    allowed_writes = {"/api/config", "/api/model-profiles", "/api/audio/settings",
                      "/api/memory-hub", "/api/auth/login", "/api/auth/logout"}

    @app.middleware("http")
    async def isolated_preview(request, call_next):
        path = request.url.path
        if path.startswith("/api/bilibili/account"):
            if request.headers.get("authorization") != "Bearer " + PREVIEW_TOKEN:
                return JSONResponse({"detail": "请先连接本地预览"}, 401)
            if path.endswith("/qr"):
                return Response('<svg xmlns="http://www.w3.org/2000/svg" width="230" height="230">'
                    '<rect width="230" height="230" fill="#eee7f8"/>'
                    '<text x="115" y="110" text-anchor="middle" fill="#7965ad" font-size="18">'
                    '本地模拟二维码</text><text x="115" y="140" text-anchor="middle" '
                    'fill="#7965ad" font-size="12">无需扫码，不连接B站</text></svg>',
                    media_type="image/svg+xml")
            body = await request.json()
            if body.get("action") == "login":
                account.update(qr_state="waiting_scan", qr_expires=time.time() + 180)
            elif body.get("action") == "poll":
                counters["polls"] += 1
                print(f"Preview QR polls: {counters['polls']}", flush=True)
            elif body.get("action") == "logout":
                account.update(qr_state="", state="anonymous")
            return JSONResponse(account)
        if path == "/api/bilibili" and request.method == "GET":
            if request.headers.get("authorization") != "Bearer " + PREVIEW_TOKEN:
                return JSONResponse({"detail": "请先连接本地预览"}, 401)
            return JSONResponse({**runtime.bilibili.snapshot(), "account": account})
        if path.startswith("/api/") and request.method != "GET" and path not in allowed_writes:
            return JSONResponse({"detail": "本地界面预览不会调用模型、访问外部平台或发送微信"}, 409)
        response = await call_next(request)
        if path == "/" and response.status_code == 200:
            # Inject a fixture label without changing the production template.
            body = b"".join([chunk async for chunk in response.body_iterator]).decode("utf-8")
            body = body.replace('<body class="studio-console">', '<body class="studio-console">'
                '<div class="studio-preview-label">本地预览 · 全部为合成资料 · 不发送微信</div>')
            return Response(body, media_type="text/html", headers={"Cache-Control": "no-store"})
        return response

    app.state.preview_counters = counters
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18120)
    args = parser.parse_args()
    parent = ROOT / "artifacts/studio-ui-20261110"
    parent.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="preview-", dir=parent))
    os.environ["WHALEGIRL_PUBLIC_ORIGINS"] = f"http://127.0.0.1:{args.port}"
    app = preview_app(data)
    print(f"Local preview: http://127.0.0.1:{args.port}/#access={PREVIEW_TOKEN}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
