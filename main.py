from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Any, Dict
from instagrapi import Client
from instagrapi.types import StoryLink
import httpx
import tempfile
import os

app = FastAPI()
SERVICE_SECRET = os.getenv("SERVICE_SECRET", "")


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


@app.post("/story")
async def post_story(req: StoryRequest, x_secret: str = Header(default=None)):
    if SERVICE_SECRET and x_secret != SERVICE_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    cl = Client()

    if req.proxy_url:
        cl.set_proxy(req.proxy_url)

    # Try to restore session, fallback to fresh login
    logged_in = False
    if req.session:
        try:
            cl.set_settings(req.session)
            cl.login(req.username, req.password)
            logged_in = True
        except Exception:
            cl.set_settings({})

    if not logged_in:
        try:
            cl.login(req.username, req.password)
        except Exception as e:
            err = str(e)
            if "bad_password" in err.lower() or "BadPassword" in err:
                raise HTTPException(status_code=400, detail="Senha incorreta.")
            if "challenge" in err.lower() or "Challenge" in err:
                raise HTTPException(status_code=400, detail="Instagram pediu verificação de segurança. Abra o app e confirme o login.")
            raise HTTPException(status_code=400, detail=f"Erro no login: {err}")

    # Download media
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
