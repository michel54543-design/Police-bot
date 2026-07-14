import os

from aiohttp import web


async def health_check(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "Police Bot"})


async def start_web_server() -> web.AppRunner:
    """Start the lightweight HTTP server required by Render Web Service."""
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    return runner
