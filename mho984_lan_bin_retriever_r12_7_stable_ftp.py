# SPDX-License-Identifier: MIT
# Independent, unofficial compatibility tool; not affiliated with RIGOL.

from __future__ import annotations
import ftplib, json, os, re, shutil, socket, ssl, struct, subprocess, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

VERSION="2026.10.04-r12.7-public-beta4-dynamic-size-wait"
ALLOW_FALLBACK_DISCOVERY=False
RG03_MAGIC=b"RG03"
HTTP_TIMEOUT_S=3.0
TCP_TIMEOUT_S=1.5
FTP_TIMEOUT_S=15.0
FTP_FILE_APPEAR_TIMEOUT_S=180.0
FTP_FILE_APPEAR_POLL_S=1.0
# Dynamic remote-file completion policy. The current firmware can expose a
# large RG03 file through FTP while it is still growing. A fixed 60-second
# stable-SIZE deadline is therefore unsafe for deep-memory captures.
FTP_SIZE_POLL_S=0.50
FTP_SIZE_STABLE_REQUIRED=6
FTP_SIZE_INITIAL_MIN_WAIT_S=60.0
FTP_SIZE_UNKNOWN_HEADER_WAIT_S=120.0
FTP_SIZE_ABSOLUTE_MAX_WAIT_S=600.0
FTP_SIZE_FORECAST_MIN_RATE_BPS=8*1024*1024
FTP_SIZE_FORECAST_SAFETY=1.75
FTP_SIZE_FORECAST_MARGIN_S=20.0
DOWNLOAD_CHUNK=4*1024*1024
HTTP_FILE_ROOTS=["/","/C:/","/C/","/data/UserData/","/storage/","/storage/user/","/userdata/"]
WEBDAV_ROOTS=["/","/C:/","/data/UserData/","/storage/"]
FTP_ROOTS=["/","/C","/C:","/data/UserData","/storage","/userdata"]
COMMON_SMB_SHARES=["C","C$","UserData","userdata","data","storage","RIGOL","rigol","public","share"]

def expected_variant_names(expected_name):
    p=Path(expected_name); out=[p.name]
    out += [f"{p.stem}{i}{p.suffix}" for i in range(10)]
    out += [f"{p.stem}00{p.suffix}",f"{p.stem}01{p.suffix}"]
    seen=set(); ans=[]
    for n in out:
        k=n.lower()
        if k not in seen:
            seen.add(k); ans.append(n)
    return ans

def variant_regex(expected_name):
    p=Path(expected_name)
    return re.compile(r"^"+re.escape(p.stem)+r"(?:\d+)?"+re.escape(p.suffix)+r"$",re.I)

def tcp_probe(host,port):
    t=time.monotonic()
    try:
        with socket.create_connection((host,port),timeout=TCP_TIMEOUT_S): pass
        return {"open":True,"elapsed_s":time.monotonic()-t}
    except Exception as e:
        return {"open":False,"elapsed_s":time.monotonic()-t,"error":f"{type(e).__name__}: {e}"}

def probe_ports(host):
    return {str(p):tcp_probe(host,p) for p in (21,22,80,111,139,443,445,5555)}

def ssl_ctx():
    c=ssl.create_default_context(); c.check_hostname=False; c.verify_mode=ssl.CERT_NONE; return c

def request(url,method="GET",headers=None,data=None,timeout=HTTP_TIMEOUT_S):
    req=urllib.request.Request(url=url,method=method,headers=headers or {},data=data)
    return urllib.request.urlopen(req,timeout=timeout,context=ssl_ctx() if url.startswith("https://") else None)

def download_http_rg03(url,dst):
    temp=dst.with_suffix(dst.suffix+".part")
    try:
        with request(url,timeout=10.0) as r:
            magic=r.read(4)
            if magic!=RG03_MAGIC:
                return {"success":False,"url":url,"reason":f"magic={magic!r}","status":getattr(r,"status",None)}
            dst.parent.mkdir(parents=True,exist_ok=True)
            with open(temp,"wb") as f:
                f.write(magic)
                while True:
                    b=r.read(DOWNLOAD_CHUNK)
                    if not b: break
                    f.write(b)
        if temp.stat().st_size<=16:
            temp.unlink(missing_ok=True); return {"success":False,"url":url,"reason":"too small"}
        os.replace(temp,dst)
        return {"success":True,"url":url,"destination":str(dst),"size":dst.stat().st_size}
    except Exception as e:
        temp.unlink(missing_ok=True)
        return {"success":False,"url":url,"error":f"{type(e).__name__}: {e}"}

def http_direct_retrieve(host,expected_name,destination_dir,ports):
    attempts=[]; schemes=[]
    if ports.get("80",{}).get("open"): schemes.append("http")
    if ports.get("443",{}).get("open"): schemes.append("https")
    for scheme in schemes:
        for root in HTTP_FILE_ROOTS:
            for name in expected_variant_names(expected_name):
                url=f"{scheme}://{host}{root}{urllib.parse.quote(name)}"
                try:
                    with request(url,headers={"Range":"bytes=0-3"}) as r:
                        magic=r.read(4)
                        if magic!=RG03_MAGIC: continue
                except urllib.error.HTTPError as e:
                    if e.code not in (400,404): attempts.append({"url":url,"http_error":e.code})
                    continue
                except Exception as e:
                    attempts.append({"url":url,"error":f"{type(e).__name__}: {e}"}); continue
                result=download_http_rg03(url,destination_dir/name)
                if result.get("success"):
                    return {"success":True,"method":"http_static","result":result,"attempts":attempts}
    return {"success":False,"method":"http_static","attempts":attempts}

def webdav_propfind(url):
    body=(b'<?xml version="1.0" encoding="utf-8"?>'
          b'<D:propfind xmlns:D="DAV:"><D:prop><D:displayname/>'
          b'<D:getcontentlength/><D:resourcetype/></D:prop></D:propfind>')
    try:
        with request(url,method="PROPFIND",headers={"Depth":"1","Content-Type":"application/xml"},data=body) as r:
            return {"success":True,"status":getattr(r,"status",None),"body":r.read(2*1024*1024),"dav":r.headers.get("DAV")}
    except urllib.error.HTTPError as e:
        return {"success":False,"http_error":e.code,"dav":e.headers.get("DAV") if e.headers else None}
    except Exception as e:
        return {"success":False,"error":f"{type(e).__name__}: {e}"}

def extract_hrefs(body):
    try: root=ET.fromstring(body)
    except Exception: return []
    return [e.text.strip() for e in root.iter() if e.tag.lower().endswith("href") and e.text]

def webdav_retrieve(host,expected_name,destination_dir,ports):
    rx=variant_regex(expected_name); attempts=[]; schemes=[]
    if ports.get("80",{}).get("open"): schemes.append("http")
    if ports.get("443",{}).get("open"): schemes.append("https")
    for scheme in schemes:
        for rp in WEBDAV_ROOTS:
            root_url=f"{scheme}://{host}{rp}"
            pr=webdav_propfind(root_url)
            attempts.append({k:v for k,v in pr.items() if k!="body"}|{"url":root_url})
            if not pr.get("success"): continue
            for href in extract_hrefs(pr["body"]):
                name=Path(urllib.parse.unquote(href).rstrip("/")).name
                if not rx.fullmatch(name): continue
                url=urllib.parse.urljoin(root_url,href)
                result=download_http_rg03(url,destination_dir/name)
                if result.get("success"):
                    return {"success":True,"method":"webdav","result":result,"attempts":attempts}
    return {"success":False,"method":"webdav","attempts":attempts}

def discover_web_control(host,ports):
    out=[]
    for scheme,port in (("http",80),("https",443)):
        if not ports.get(str(port),{}).get("open"): continue
        url=f"{scheme}://{host}/"
        try:
            with request(url,timeout=5.0) as r:
                raw=r.read(1024*1024); text=raw.decode("utf-8","replace")
                tm=re.search(r"<title[^>]*>(.*?)</title>",text,re.I|re.S)
                scripts=re.findall(r"<script[^>]+src=[\"']([^\"']+)",text,re.I)
                interesting=sorted(set(re.findall(r"""[\"']([^\"']*(?:download|file|storage|userdata|webdav)[^\"']*)[\"']""",text,re.I)))[:50]
                out.append({"url":url,"status":getattr(r,"status",None),"server":r.headers.get("Server"),
                            "title":re.sub(r"\s+"," ",tm.group(1)).strip() if tm else None,
                            "scripts":scripts[:100],"interesting_strings":interesting})
        except Exception as e:
            out.append({"url":url,"error":f"{type(e).__name__}: {e}"})
    return {"results":out}

def parse_net_view_shares(text):
    out=[]
    for line in text.splitlines():
        s=line.strip()
        if not s or set(s)<={"-"} or s.startswith("\\"): continue
        first=s.split()[0]
        if re.fullmatch(r"[A-Za-z0-9_$.-]{1,80}",first): out.append(first)
    return out

def smb_retrieve_windows(host,expected_name,destination_dir,ports):
    if os.name!="nt": return {"success":False,"method":"windows_smb_server","skipped":"not_windows"}
    if not (ports.get("445",{}).get("open") or ports.get("139",{}).get("open")):
        return {"success":False,"method":"windows_smb_server","skipped":"ports_closed"}
    shares=list(COMMON_SMB_SHARES); net_view={}
    try:
        cp=subprocess.run(["net","view",rf"\\{host}"],capture_output=True,text=True,timeout=10,encoding="utf-8",errors="replace")
        net_view={"returncode":cp.returncode,"stdout":cp.stdout,"stderr":cp.stderr}
        shares+=parse_net_view_shares(cp.stdout)
    except Exception as e: net_view={"error":f"{type(e).__name__}: {e}"}
    shares=list(dict.fromkeys(shares))
    for share in shares:
        for name in expected_variant_names(expected_name):
            unc=Path(rf"\\{host}\{share}\{name}")
            try:
                if not unc.exists(): continue
                with open(unc,"rb") as f:
                    if f.read(4)!=RG03_MAGIC: continue
                dst=destination_dir/name; dst.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(unc,dst)
                return {"success":True,"method":"windows_smb_server","source":str(unc),"destination":str(dst),
                        "size":dst.stat().st_size,"net_view":net_view,"shares_tested":shares}
            except Exception: pass
    return {"success":False,"method":"windows_smb_server","net_view":net_view,"shares_tested":shares}

def _ftp_open_anonymous(host):
    ftp=ftplib.FTP()
    ftp.connect(host,21,timeout=FTP_TIMEOUT_S)
    welcome=ftp.getwelcome()
    ftp.login("anonymous","mho984-r12.1@localhost")
    ftp.voidcmd("TYPE I")
    return ftp,welcome


def _ftp_find_matching_file(ftp,expected_name):
    rx=variant_regex(expected_name)
    attempts=[]

    for root in FTP_ROOTS:
        try:
            ftp.cwd("/")
            if root!="/":
                ftp.cwd(root)
            names=ftp.nlst()
        except Exception as e:
            attempts.append({
                "root":root,
                "error":f"{type(e).__name__}: {e}",
            })
            continue

        matches=[]
        for remote in names:
            name=Path(remote.rstrip("/")).name
            if rx.fullmatch(name):
                matches.append((remote,name))

        if not matches:
            continue

        exact=[
            item for item in matches
            if item[1].lower()==Path(expected_name).name.lower()
        ]
        if exact:
            return root,exact[0][0],exact[0][1],attempts

        matches.sort(key=lambda item:(len(item[1]),item[1].lower()))
        return root,matches[0][0],matches[0][1],attempts

    return None,None,None,attempts


def _select_match_from_names(names, expected_name):
    rx=variant_regex(expected_name)
    matches=[]
    for remote in names:
        name=Path(str(remote).rstrip("/")).name
        if rx.fullmatch(name):
            matches.append((remote,name))
    if not matches:
        return None
    exact=[
        item for item in matches
        if item[1].lower()==Path(expected_name).name.lower()
    ]
    if exact:
        return exact[0]
    matches.sort(key=lambda item:(len(item[1]),item[1].lower()))
    return matches[0]


def _wait_ftp_matching_file(
    host,
    expected_name,
    timeout_s=FTP_FILE_APPEAR_TIMEOUT_S,
    poll_s=FTP_FILE_APPEAR_POLL_S,
):
    """Wait until the saved BIN becomes visible in the FTP namespace."""
    started=time.perf_counter()
    history=[]
    viable_roots=None
    initial_attempts=[]
    ftp=None
    welcome=None
    reconnects=0
    try:
        while time.perf_counter()-started<timeout_s:
            if ftp is None:
                try:
                    ftp,welcome=_ftp_open_anonymous(host)
                    reconnects+=1
                except Exception as e:
                    history.append({"elapsed_s":round(time.perf_counter()-started,3),"event":"ftp_connect_failed","error":f"{type(e).__name__}: {e}"})
                    time.sleep(poll_s)
                    continue
            roots_to_check=FTP_ROOTS if viable_roots is None else viable_roots
            cycle_viable=[]
            cycle_counts={}
            matched=None
            try:
                for root in roots_to_check:
                    try:
                        ftp.cwd("/")
                        if root!="/":
                            ftp.cwd(root)
                        names=ftp.nlst()
                        cycle_viable.append(root)
                        cycle_counts[root]=len(names)
                    except Exception as e:
                        if viable_roots is None:
                            initial_attempts.append({"root":root,"error":f"{type(e).__name__}: {e}"})
                        continue
                    selected=_select_match_from_names(names,expected_name)
                    if selected is not None:
                        remote,name=selected
                        matched=(root,remote,name)
                        break
                if viable_roots is None:
                    viable_roots=cycle_viable or ["/"]
                elapsed=time.perf_counter()-started
                history.append({"elapsed_s":round(elapsed,3),"event":"directory_poll","roots_checked":list(roots_to_check),"entry_counts":cycle_counts,"found":bool(matched)})
                if matched is not None:
                    root,remote,name=matched
                    return {"found":True,"root":root,"remote":remote,"name":name,"elapsed_s":elapsed,"poll_count":sum(1 for h in history if h.get("event")=="directory_poll"),"reconnects":reconnects,"welcome":welcome,"initial_attempts":initial_attempts,"history":history}
            except Exception as e:
                history.append({"elapsed_s":round(time.perf_counter()-started,3),"event":"ftp_session_error","error":f"{type(e).__name__}: {e}"})
                try: ftp.close()
                except Exception: pass
                ftp=None
                viable_roots=None
            time.sleep(poll_s)
        return {"found":False,"reason":"timeout_waiting_for_file_to_appear","elapsed_s":time.perf_counter()-started,"poll_count":sum(1 for h in history if h.get("event")=="directory_poll"),"reconnects":reconnects,"initial_attempts":initial_attempts,"history":history}
    finally:
        try:
            if ftp is not None: ftp.quit()
        except Exception:
            try:
                if ftp is not None: ftp.close()
            except Exception: pass


def _parse_rg03_file_header(header16):
    if len(header16)<16:
        return {
            "valid":False,
            "reason":f"short_header:{len(header16)}",
        }

    magic=header16[:4]
    if magic!=RG03_MAGIC:
        return {
            "valid":False,
            "reason":f"magic={magic!r}",
        }

    total_file_bytes,reserved,waveform_count=struct.unpack_from(
        "<III",header16,4
    )

    if total_file_bytes<16:
        return {
            "valid":False,
            "reason":f"implausible_total:{total_file_bytes}",
        }

    if not (1<=waveform_count<=64):
        return {
            "valid":False,
            "reason":f"implausible_waveform_count:{waveform_count}",
        }

    return {
        "valid":True,
        "magic":"RG03",
        "total_file_bytes":int(total_file_bytes),
        "reserved":int(reserved),
        "waveform_count":int(waveform_count),
    }


def _ftp_read_first16(host,root,remote):
    """
    Read only the first 16 bytes using a disposable FTP connection.

    Closing RETR early may leave a pending FTP response. The entire control
    connection is therefore discarded immediately afterwards.
    """
    ftp=None
    data_sock=None

    try:
        ftp,_=_ftp_open_anonymous(host)
        ftp.cwd("/")
        if root!="/":
            ftp.cwd(root)

        data_sock=ftp.transfercmd(f"RETR {remote}")
        header=b""

        while len(header)<16:
            chunk=data_sock.recv(16-len(header))
            if not chunk:
                break
            header+=chunk

        return _parse_rg03_file_header(header)

    except Exception as e:
        return {
            "valid":False,
            "reason":f"{type(e).__name__}: {e}",
        }

    finally:
        try:
            if data_sock is not None:
                data_sock.close()
        except Exception:
            pass
        try:
            if ftp is not None:
                ftp.close()
        except Exception:
            pass


def _ftp_query_size(ftp,remote):
    try:
        value=ftp.size(remote)
        return None if value is None else int(value)
    except Exception:
        return None


def _initial_dynamic_size_wait_budget(header_info):
    """Return a capacity-aware initial wait budget in seconds.

    The RG03 header size is used only to choose how long we are willing to
    observe a growing file. It is deliberately NOT used as the completion
    target because validated MHO984 captures have shown that the header total
    and final FTP SIZE can differ.
    """
    if isinstance(header_info,dict) and header_info.get("valid"):
        total=int(header_info.get("total_file_bytes") or 0)
        if total>0:
            estimate=(total/float(FTP_SIZE_FORECAST_MIN_RATE_BPS)*FTP_SIZE_FORECAST_SAFETY+FTP_SIZE_FORECAST_MARGIN_S)
            return min(FTP_SIZE_ABSOLUTE_MAX_WAIT_S,max(FTP_SIZE_INITIAL_MIN_WAIT_S,estimate))
    return min(FTP_SIZE_ABSOLUTE_MAX_WAIT_S,max(FTP_SIZE_INITIAL_MIN_WAIT_S,FTP_SIZE_UNKNOWN_HEADER_WAIT_S))


def _wait_remote_rg03_complete(host,root,remote,poll_s=FTP_SIZE_POLL_S,stable_required=FTP_SIZE_STABLE_REQUIRED,absolute_max_wait_s=FTP_SIZE_ABSOLUTE_MAX_WAIT_S):
    """Wait for a remotely growing RG03 file to become stable."""
    started=time.perf_counter()
    hard_deadline=started+float(absolute_max_wait_s)
    history=[]
    try: header_info=_ftp_read_first16(host,root,remote)
    except Exception: header_info=None
    initial_budget=_initial_dynamic_size_wait_budget(header_info)
    soft_deadline=min(hard_deadline,started+initial_budget)
    ftp=None
    last_size=None
    stable_count=0
    last_growth_time=None
    last_growth_size=None
    growth_rate_ema=None
    max_soft_deadline=soft_deadline
    try:
        ftp,_=_ftp_open_anonymous(host)
        ftp.cwd("/")
        if root!="/": ftp.cwd(root)
        while True:
            now=time.perf_counter()
            elapsed=now-started
            size=_ftp_query_size(ftp,remote)
            grew=False
            instantaneous_rate=None
            if size is None:
                stable_count=0
            else:
                if size>0 and size==last_size: stable_count+=1
                else: stable_count=1 if size>0 else 0
                if last_size is not None and size>last_size:
                    grew=True
                    if last_growth_time is not None and last_growth_size is not None:
                        dt=max(now-last_growth_time,1e-9)
                        db=size-last_growth_size
                        instantaneous_rate=db/dt
                        if instantaneous_rate>0:
                            if growth_rate_ema is None: growth_rate_ema=instantaneous_rate
                            else: growth_rate_ema=(0.25*instantaneous_rate)+(0.75*growth_rate_ema)
                    last_growth_time=now
                    last_growth_size=size
                declared=(int(header_info.get("total_file_bytes")) if isinstance(header_info,dict) and header_info.get("valid") and header_info.get("total_file_bytes") is not None else None)
                if size is not None and size>0 and declared is not None and declared>size and growth_rate_ema is not None and growth_rate_ema>0:
                    remaining=declared-size
                    forecast_remaining=remaining/growth_rate_ema
                    candidate=now+forecast_remaining*FTP_SIZE_FORECAST_SAFETY+FTP_SIZE_FORECAST_MARGIN_S
                    soft_deadline=min(hard_deadline,max(soft_deadline,candidate))
                    max_soft_deadline=max(max_soft_deadline,soft_deadline)
            declared_for_log=header_info.get("total_file_bytes") if isinstance(header_info,dict) else None
            history.append({"elapsed_s":round(elapsed,3),"remote_size":size,"stable_count":stable_count,"grew":grew,"instantaneous_rate_Bps":None if instantaneous_rate is None else round(instantaneous_rate,3),"growth_rate_ema_Bps":None if growth_rate_ema is None else round(growth_rate_ema,3),"header_declared_total":declared_for_log,"soft_deadline_elapsed_s":round(soft_deadline-started,3),"hard_deadline_elapsed_s":round(hard_deadline-started,3)})
            if size is not None and size>0 and stable_count>=stable_required:
                return {"complete":True,"method":"FTP_SIZE_dynamic_stable","remote_size":int(size),"stable_count":stable_count,"header":header_info,"header_size_matches_remote":bool(isinstance(header_info,dict) and header_info.get("valid") and header_info.get("total_file_bytes")==int(size)),"initial_wait_budget_s":initial_budget,"final_soft_deadline_s":max_soft_deadline-started,"absolute_max_wait_s":float(absolute_max_wait_s),"observed_growth_rate_Bps":growth_rate_ema,"history":history,"elapsed_s":time.perf_counter()-started}
            now=time.perf_counter()
            if now>=hard_deadline:
                return {"complete":False,"reason":"absolute_timeout_waiting_for_stable_FTP_SIZE","remote_size":last_size,"header":header_info,"initial_wait_budget_s":initial_budget,"final_soft_deadline_s":max_soft_deadline-started,"absolute_max_wait_s":float(absolute_max_wait_s),"observed_growth_rate_Bps":growth_rate_ema,"history":history,"elapsed_s":now-started}
            if now>=soft_deadline:
                recent_growth=last_growth_time is not None and (now-last_growth_time)<=max(5.0,poll_s*stable_required*2)
                if recent_growth:
                    soft_deadline=min(hard_deadline,now+max(30.0,poll_s*stable_required*4))
                    max_soft_deadline=max(max_soft_deadline,soft_deadline)
                else:
                    return {"complete":False,"reason":"dynamic_timeout_waiting_for_stable_FTP_SIZE","remote_size":last_size,"header":header_info,"initial_wait_budget_s":initial_budget,"final_soft_deadline_s":max_soft_deadline-started,"absolute_max_wait_s":float(absolute_max_wait_s),"observed_growth_rate_Bps":growth_rate_ema,"history":history,"elapsed_s":now-started}
            last_size=size
            time.sleep(poll_s)
    finally:
        try:
            if ftp is not None: ftp.quit()
        except Exception:
            try:
                if ftp is not None: ftp.close()
            except Exception: pass

def _validate_downloaded_rg03(path, expected_remote_size=None):
    """
    Validate transport integrity only.

    The RG03 file-level total_file_bytes field is diagnostic because the real
    MHO984 produced a structurally plausible file whose FTP SIZE remained
    permanently different from that field.

    Final format integrity is checked by mho984_bin_decoder_r12_2.py, which
    parses every waveform/data header and requires the parsed end offset to
    equal the actual EOF.
    """
    path=Path(path)
    try:
        actual=path.stat().st_size
        with open(path,"rb") as f:
            header=f.read(16)

        info=_parse_rg03_file_header(header)

        if not info.get("valid"):
            return {
                "valid":False,
                "reason":"invalid_RG03_header",
                "actual_size":actual,
                "header":info,
            }

        if expected_remote_size is not None and actual!=int(expected_remote_size):
            return {
                "valid":False,
                "reason":"local_size_differs_from_stable_remote_size",
                "actual_size":actual,
                "expected_remote_size":int(expected_remote_size),
                "header":info,
            }

        return {
            "valid":True,
            "actual_size":actual,
            "expected_remote_size":(
                None
                if expected_remote_size is None
                else int(expected_remote_size)
            ),
            "header_declared_total":int(info["total_file_bytes"]),
            "header_size_matches_actual":(
                int(info["total_file_bytes"])==actual
            ),
            "header_declared_minus_actual":(
                int(info["total_file_bytes"])-actual
            ),
            "header":info,
        }

    except Exception as e:
        return {
            "valid":False,
            "error":f"{type(e).__name__}: {e}",
        }

def _download_one_ftp(host,root,remote,dst,expected_remote_size=None):
    tmp=dst.with_suffix(dst.suffix+".part")
    tmp.unlink(missing_ok=True)
    ftp=None
    started=time.perf_counter()

    try:
        ftp,welcome=_ftp_open_anonymous(host)
        ftp.cwd("/")
        if root!="/":
            ftp.cwd(root)

        with open(tmp,"wb") as f:
            ftp.retrbinary(
                f"RETR {remote}",
                f.write,
                blocksize=DOWNLOAD_CHUNK,
            )

        validation=_validate_downloaded_rg03(tmp, expected_remote_size)
        if not validation.get("valid"):
            tmp.unlink(missing_ok=True)
            return {
                "success":False,
                "reason":"downloaded_RG03_length_validation_failed",
                "validation":validation,
            }

        os.replace(tmp,dst)
        elapsed=time.perf_counter()-started
        size=dst.stat().st_size

        return {
            "success":True,
            "welcome":welcome,
            "destination":str(dst),
            "size":size,
            "elapsed_s":elapsed,
            "throughput_MiB_s":(
                size/(1024*1024)/elapsed
                if elapsed>0
                else None
            ),
            "validation":validation,
        }

    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "success":False,
            "error":f"{type(e).__name__}: {e}",
        }

    finally:
        try:
            if ftp is not None:
                ftp.quit()
        except Exception:
            try:
                if ftp is not None:
                    ftp.close()
            except Exception:
                pass


def ftp_retrieve(host,expected_name,destination_dir,ports):
    if not ports.get("21",{}).get("open"):
        return {"success":False,"method":"anonymous_ftp","skipped":"port_closed"}

    appearance=_wait_ftp_matching_file(host,expected_name,timeout_s=FTP_FILE_APPEAR_TIMEOUT_S,poll_s=FTP_FILE_APPEAR_POLL_S)
    attempts=appearance.get("initial_attempts",[])
    if not appearance.get("found"):
        return {"success":False,"method":"anonymous_ftp","reason":"matching_file_not_found_after_wait","appearance_wait":appearance,"attempts":attempts}
    root=appearance["root"]
    remote=appearance["remote"]
    name=appearance["name"]

    destination_dir=Path(destination_dir)
    destination_dir.mkdir(parents=True,exist_ok=True)
    dst=destination_dir/name
    transfer_attempts=[]
    for transfer_index in range(1,4):
        readiness=_wait_remote_rg03_complete(host,root,remote,poll_s=FTP_SIZE_POLL_S,stable_required=FTP_SIZE_STABLE_REQUIRED,absolute_max_wait_s=FTP_SIZE_ABSOLUTE_MAX_WAIT_S)
        if not readiness.get("complete"):
            return {"success":False,"method":"anonymous_ftp","reason":"remote_file_not_complete","root":root,"remote":remote,"readiness":readiness,"appearance_wait":appearance,"attempts":attempts,"transfer_attempts":transfer_attempts}
        download=_download_one_ftp(host,root,remote,dst,expected_remote_size=readiness.get("remote_size"))
        transfer_attempts.append({"index":transfer_index,"readiness":readiness,"download":download})
        if download.get("success"):
            return {"success":True,"method":"anonymous_ftp","root":root,"remote":remote,"destination":download["destination"],"size":download["size"],"elapsed_s":download["elapsed_s"],"throughput_MiB_s":download["throughput_MiB_s"],"appearance_wait":appearance,"readiness":readiness,"download_validation":download["validation"],"transfer_attempts":transfer_attempts}
        time.sleep(1.0)
    return {"success":False,"method":"anonymous_ftp","reason":"complete_RG03_download_failed_after_retries","root":root,"remote":remote,"appearance_wait":appearance,"attempts":attempts,"transfer_attempts":transfer_attempts}

def retrieve_bin(host,expected_name,destination_dir,report_path=None):
    destination_dir=Path(destination_dir)
    destination_dir.mkdir(parents=True,exist_ok=True)

    started_perf=time.perf_counter()
    report={
        "tool":"MHO984 LAN BIN Retriever",
        "version":VERSION,
        "strategy":"anonymous_FTP_file_appearance_wait_then_capacity_aware_dynamic_stable_SIZE; RG03 record-structure validation in decoder",
        "started_at":datetime.now().astimezone().isoformat(timespec="seconds"),
        "host":host,
        "expected_name":expected_name,
        "variant_names":expected_variant_names(expected_name),
    }

    # R12 fast path:
    # The user's MHO984 firmware has already proven anonymous FTP retrieval.
    # Avoid 8-port scan + Web Control + WebDAV + HTTP + SMB attempts on every run.
    fast_started=time.perf_counter()
    ftp_fast=ftp_retrieve(
        host,
        expected_name,
        destination_dir,
        {"21":{"open":True}},
    )
    report["fast_ftp"]=ftp_fast
    report["fast_ftp_elapsed_s"]=time.perf_counter()-fast_started

    if ftp_fast.get("success"):
        report["success"]=True
        report["successful_method"]="anonymous_ftp_fast_path"
        report["local_path"]=ftp_fast["destination"]
        report["fallback_discovery_used"]=False
        report["total_elapsed_s"]=time.perf_counter()-started_perf
        report["completed_at"]=datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        if report_path:
            Path(report_path).write_text(
                json.dumps(report,ensure_ascii=False,indent=2),
                encoding="utf-8"
            )
        return report

    # Public-release safety policy: do not probe unrelated services/ports unless
    # the user explicitly opts in. The normal path uses only anonymous FTP/21.
    if not ALLOW_FALLBACK_DISCOVERY:
        report["success"]=False
        report["successful_method"]=None
        report["fallback_discovery_used"]=False
        report["conclusion"]=(
            "Anonymous FTP retrieval failed. Broad fallback discovery is disabled "
            "by default in the public release. Re-run with explicit opt-in only "
            "on a trusted, authorized lab network."
        )
        report["total_elapsed_s"]=time.perf_counter()-started_perf
        report["completed_at"]=datetime.now().astimezone().isoformat(timespec="seconds")
        if report_path:
            Path(report_path).write_text(
                json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"
            )
        return report

    # Compatibility fallback, explicit opt-in only.
    report["fallback_discovery_used"]=True
    report["ports"]=probe_ports(host)
    report["web_control"]=discover_web_control(host,report["ports"])

    methods=[
        ("webdav",webdav_retrieve),
        ("http_static",http_direct_retrieve),
        ("windows_smb_server",smb_retrieve_windows),
    ]

    for name,fn in methods:
        result=fn(host,expected_name,destination_dir,report["ports"])
        report.setdefault("methods",{})[name]=result
        if result.get("success"):
            report["success"]=True
            report["successful_method"]=name
            report["local_path"]=(
                result["result"]["destination"]
                if "result" in result
                else result["destination"]
            )
            report["total_elapsed_s"]=time.perf_counter()-started_perf
            report["completed_at"]=datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            if report_path:
                Path(report_path).write_text(
                    json.dumps(report,ensure_ascii=False,indent=2),
                    encoding="utf-8"
                )
            return report

    report["success"]=False
    report["successful_method"]=None
    report["conclusion"]="Fast anonymous FTP and fallback read-only LAN methods failed."
    report["total_elapsed_s"]=time.perf_counter()-started_perf
    report["completed_at"]=datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    if report_path:
        Path(report_path).write_text(
            json.dumps(report,ensure_ascii=False,indent=2),
            encoding="utf-8"
        )

    return report
