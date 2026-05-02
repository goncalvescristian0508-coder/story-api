from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Any, Dict
from instagrapi import Client
from instagrapi.types import StoryLink
import httpx
import tempfile
import os
import time
import random

app = FastAPI()
SERVICE_SECRET = os.getenv("SERVICE_SECRET", "")


def check_secret(x_secret: Optional[str]):
    if SERVICE_SECRET and x_secret != SERVICE_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


def make_client(proxy_url: Optional[str], session: Optional[Dict[str, Any]]) -> tuple[Client, bool]:
    cl = Client()
    cl.set_locale("pt_BR")
    cl.set_timezone_offset(-10800)  # UTC-3 Brazil
    if proxy_url:
        cl.set_proxy(proxy_url)
    restored = False
    if session:
        try:
            cl.set_settings(session)
            restored = True
        except Exception:
            cl.set_settings({})
    return cl, restored


def do_login(cl: Client, username: str, password: str, restored: bool) -> None:
    if restored:
        try:
            cl.login(username, password)
            return
        except Exception:
            cl.set_settings({})

    time.sleep(random.uniform(2, 4))
    try:
        cl.login(username, password)
    except Exception as e:
        err = str(e)
        err_lower = err.lower()
        if "bad_password" in err_lower or "badpassword" in err_lower:
            raise HTTPException(status_code=400, detail="Usuário ou senha incorretos.")
        if "challenge" in err_lower:
            raise HTTPException(status_code=400, detail="Instagram pediu verificação de segurança. Abra o app e confirme o login.")
        if "two_factor" in err_lower or "twofactor" in err_lower:
            raise HTTPException(status_code=400, detail="Esta conta tem 2FA ativo. Desative o 2FA e tente novamente.")
        if "blacklist" in err_lower or "ip" in err_lower:
            raise HTTPException(status_code=400, detail="IP bloqueado pelo Instagram. Tente com outro proxy ou aguarde.")
        raise HTTPException(status_code=400, detail=f"Erro no login: {err}")


class LoginRequest(BaseModel):
    username: str
    password: str
    proxy_url: Optional[str] = None
    session: Optional[Dict[str, Any]] = None


class StoryRequest(BaseModel):
    username: str
    password: str
    media_url: str
    is_video: bool = False
    link_url: Optional[str] = None
    proxy_url: Optional[str] = None
    session: Optional[Dict[str, Any]] = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/login")
async def login(req: LoginRequest, x_secret: str = Header(default=None)):
    check_secret(x_secret)
    cl, restored = make_client(req.proxy_url, req.session)
    do_login(cl, req.username, req.password, restored)
    return {"ok": True, "session": cl.get_settings()}


@app.post("/story")
async def post_story(req: StoryRequest, x_secret: str = Header(default=None)):
    check_secret(x_secret)
    cl, restored = make_client(req.proxy_url, req.session)
    do_login(cl, req.username, req.password, restored)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(req.media_url)
            resp.raise_for_status()
            content = resp.content
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao baixar mídia: {str(e)}")

    suffix = ".mp4" if req.is_video else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(content)
        tmp_path = f.name

    try:
        links = [StoryLink(webUri=req.link_url)] if req.link_url else []

        if req.is_video:
            cl.video_upload_to_story(tmp_path, links=links)
        else:
            cl.photo_upload_to_story(tmp_path, links=links)

        return {"ok": True, "session": cl.get_settings()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao postar story: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
