import os, re, uuid, asyncio, subprocess, threading, shutil
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field

app=FastAPI(title="ShortForge")
BASE=Path("/tmp/shortforge"); BASE.mkdir(exist_ok=True)
CLIPS=BASE/"clips"; CLIPS.mkdir(exist_ok=True)
OUT=BASE/"videos"; OUT.mkdir(exist_ok=True)
LOCK=threading.Lock()
TOKEN=os.getenv("SHORTFORGE_TOKEN","")
def auth(authorization):
    if not TOKEN: raise HTTPException(503,"Set SHORTFORGE_TOKEN before exposing this app")
    import secrets
    if not secrets.compare_digest(authorization or "", "Bearer "+TOKEN): raise HTTPException(401,"Incorrect access token")
class Video(BaseModel):
    topic:str=Field(min_length=3,max_length=1600)
    genre:str="story"
    lang:str="pl"
    voice:str="female"
    clip_id:str|None=None
@app.get("/",response_class=HTMLResponse)
def home(): return Path("/app/index.html").read_text(encoding="utf-8")
@app.get("/api/health")
def health(): return {"ok":True,"protected":bool(TOKEN),"engine":"neural voice + FFmpeg"}
@app.post("/api/clip")
async def clip(file:UploadFile=File(...),authorization:str|None=Header(default=None)):
    auth(authorization)
    if not file.filename or not file.filename.lower().endswith(".mp4"):raise HTTPException(400,"Only MP4")
    ident=uuid.uuid4().hex;p=CLIPS/(ident+".mp4");n=0
    try:
        with p.open("wb") as f:
            while data:=await file.read(1024*1024):
                n+=len(data)
                if n>60*1024*1024:raise HTTPException(413,"Max 60MB")
                f.write(data)
        check=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=codec_type","-of","default=nw=1",str(p)],capture_output=True,text=True,timeout=20)
        if check.returncode or "video" not in check.stdout:raise HTTPException(400,"Invalid video")
        return {"id":ident,"name":file.filename}
    except Exception:
        p.unlink(missing_ok=True);raise

def script_for(v):
    t=v.topic.strip()
    if len(t)>120 or any(c in t for c in ".!?"):return t
    if v.lang=="en":
        return f"Nobody expected what happened next. {t}. It all started like an ordinary day. But within minutes, everything changed. Stay with me, because the ending is unbelievable."
    return {
    "story":f"Nie uwierzysz, jak skończyła się ta historia. {t}. Początkowo wszystko wyglądało normalnie. Jednak po chwili wydarzyło się coś, czego nikt nie przewidział. Najdziwniejsze miało dopiero nadejść. A ty, jak byś zareagował?",
    "brainrot":f"Totalna afera! {t}. Nagle wszyscy zaczęli panikować, bo pojawił się boss z kosmosu. Ale to dopiero początek. Za chwilę wydarzyło się coś absolutnie absurdalnego. Co tu się w ogóle dzieje?",
    "horror":f"To, co zdarzyło się tej nocy, nie daje mi spokoju. {t}. Najpierw dało się słyszeć cichy stukot. Potem zgasło światło. Kiedy ktoś otworzył drzwi, za nimi nie było nikogo. A jednak kamera pokazała coś innego.",
    "reddit":f"Trafiłem na dziwną historię w internecie. {t}. Autor przysięgał, że to prawda. Komentarze były podzielone, ale jeden szczegół zwrócił uwagę wszystkich. Posłuchaj do końca i oceń sam."
    }.get(v.genre,f"{t}. Ale wtedy stało się coś nieoczekiwanego.")

def ass_time(t):
    t=max(0,t);return f"{int(t//3600)}:{int(t%3600//60):02}:{int(t%60):02}.{int(t%1*100):02}"
def subtitles(script,duration,path):
    words=script.split();chunks=[" ".join(words[i:i+5]) for i in range(0,len(words),5)]
    header="""[Script Info]
ScriptType: v4.00+
PlayResX: 540
PlayResY: 960
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,36,&H00FFFFFF,&H000000FF,&H00000000,&H88000000,-1,0,0,0,100,100,0,0,1,4,1,5,25,25,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    now=.2; usable=max(duration-0.4,.5)
    for chunk in chunks:
        dt=usable*len(chunk.split())/max(1,len(words))
        clean=chunk.replace("{","(").replace("}",")").replace("\\","")
        header+=f"Dialogue: 0,{ass_time(now)},{ass_time(min(duration,now+dt))},Default,,0,0,0,,{clean}\n"
        now+=dt
    path.write_text(header,encoding="utf-8")

@app.post("/api/generate")
def generate(v:Video,authorization:str|None=Header(default=None)):
    auth(authorization)
    if not LOCK.acquire(blocking=False):raise HTTPException(429,"Another video is rendering")
    ident=uuid.uuid4().hex
    audio=BASE/(ident+".mp3");sub=BASE/(ident+".ass");dest=OUT/(ident+".mp4")
    try:
        import edge_tts
        txt=script_for(v)
        if len(txt.split())>180:txt=" ".join(txt.split()[:180])
        name=("pl-PL-ZofiaNeural" if v.voice=="female" else "pl-PL-MarekNeural") if v.lang=="pl" else ("en-US-JennyNeural" if v.voice=="female" else "en-US-GuyNeural")
        try:asyncio.run(edge_tts.Communicate(txt,name).save(str(audio)))
        except Exception:raise HTTPException(503,"Neural TTS unavailable, no robotic fallback")
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(audio)],capture_output=True,text=True,timeout=15,check=True)
        duration=min(110,max(5,float(p.stdout.strip())+.3))
        subtitles(txt,duration,sub)
        clip=CLIPS/(v.clip_id+".mp4") if v.clip_id and re.fullmatch(r"[a-f0-9]{32}",v.clip_id) else None
        if v.clip_id and (clip is None or not clip.exists()):raise HTTPException(404,"Upload a gameplay clip first")
        video=["-stream_loop","-1","-i",str(clip)] if clip else ["-f","lavfi","-i","color=c=0x101b2b:s=540x960:r=24"]
        filt="scale=540:960:force_original_aspect_ratio=increase,crop=540:960,ass="+str(sub)+",format=yuv420p"
        cmd=["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y",*video,"-i",str(audio),"-vf",filt,"-t",str(duration),"-r","24","-c:v","libx264","-preset","veryfast","-crf","27","-c:a","aac","-b:a","128k","-movflags","+faststart",str(dest)]
        q=subprocess.run(cmd,capture_output=True,timeout=180)
        if q.returncode:raise HTTPException(500,"FFmpeg render failed: "+q.stderr.decode(errors="replace")[-400:])
        return {"id":ident,"duration":round(duration,1),"url":"/api/video/"+ident}
    finally:
        audio.unlink(missing_ok=True);sub.unlink(missing_ok=True);LOCK.release()
@app.get("/api/video/{ident}")
def video(ident:str,token:str=""):
    # Browser video download needs a token query; avoid sharing public URLs.
    auth("Bearer "+token)
    if not re.fullmatch(r"[a-f0-9]{32}",ident):raise HTTPException(404)
    p=OUT/(ident+".mp4")
    if not p.exists():raise HTTPException(404)
    return FileResponse(p,media_type="video/mp4",filename="shortforge.mp4")
