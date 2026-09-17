#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bilibili Cloud Publisher - GitHub Actions 云端专属的 B 站极速发布与全自动置顶核心
支持从环境变量 BILIBILI_COOKIES 读取认证凭据，支持全球 UPOS 入口直传
"""

import os, sys, time, json, math, hashlib, requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://member.bilibili.com/",
    "Origin": "https://member.bilibili.com",
}

def get_bilibili_cookies():
    """从环境变量获取 B 站 Cookie 字典"""
    raw = os.environ.get("BILIBILI_COOKIES", "").strip()
    if not raw:
        # 本地降级读取
        if os.path.exists("/Users/bin/.gemini/antigravity/scratch/bili_cookies_cached.json"):
            with open("/Users/bin/.gemini/antigravity/scratch/bili_cookies_cached.json") as f:
                return json.load(f)
        raise ValueError("BILIBILI_COOKIES 环境变量未设置或为空！")
    return json.loads(raw)

def upload_cover_image(cookies, cover_path):
    """上传封面图并返回 cover_url"""
    if not os.path.exists(cover_path):
        print(f"[Cover] 警告: 封面文件 {cover_path} 不存在，跳过上传。")
        return ""
    
    print(f"[Cover] 正在上传官方原画封面: {cover_path}...")
    import base64
    with open(cover_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    
    url = "https://member.bilibili.com/x/vu/web/cover/up"
    data = {
        "cover": f"data:image/jpeg;base64,{img_b64}",
        "csrf": cookies.get("bili_jct", "")
    }
    res = requests.post(url, data=data, headers=HEADERS, cookies=cookies).json()
    if res.get("code") == 0:
        cover_url = res["data"]["url"]
        print(f"[Cover] 封面上传成功: {cover_url}")
        return cover_url
    else:
        print(f"[Cover] 封面上传返回异常: {res}")
        return ""

def upos_upload_video(cookies, video_path):
    """
    UPOS 分块直传视频到 B 站服务器（兼容 ugcever / ugc 所有存储桶前缀）
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")
    
    file_size = os.path.getsize(video_path)
    file_name = os.path.basename(video_path)
    print(f"[UPOS] 开始上传视频: {file_name} ({file_size/1024/1024:.2f} MB)")
    
    # 1. 预分配节点
    pre_url = f"https://member.bilibili.com/preupload?name={file_name}&size={file_size}&r=upos&profile=ugcupos%2Fbup&ssl=0&version=2.14.0&build=2140000"
    pre_res = requests.get(pre_url, headers=HEADERS, cookies=cookies).json()
    upos_uri = pre_res.get("upos_uri")
    auth = pre_res.get("auth")
    endpoint = pre_res.get("endpoint", "upos-sz-upcdnbda2.bilivideo.com")
    
    if not upos_uri or not auth:
        raise RuntimeError(f"预分配 UPOS 失败: {pre_res}")
    
    # 铁律：自适应兼容所有存储桶前缀
    upos_clean_path = upos_uri.replace("upos://", "")
    base_upload_url = f"https://{endpoint}/{upos_clean_path}"
    print(f"[UPOS] 上传端点: {base_upload_url}")
    
    # 2. 初始化上传 Session
    init_url = f"{base_upload_url}?uploads&output=json"
    init_res = requests.post(init_url, headers={"X-Upos-Auth": auth, **HEADERS}).json()
    upload_id = init_res.get("upload_id")
    if not upload_id:
        raise RuntimeError(f"初始化 UPOS 失败: {init_res}")
    
    # 3. 分块并发直传（云端使用 10MB 分块）
    chunk_size = 10 * 1024 * 1024
    total_parts = math.ceil(file_size / chunk_size)
    print(f"[UPOS] 总分块数: {total_parts}，开始高速分块传输...")
    
    parts_info = []
    with open(video_path, "rb") as f:
        for part_num in range(total_parts):
            chunk = f.read(chunk_size)
            part_url = f"{base_upload_url}?partNumber={part_num+1}&uploadId={upload_id}&chunks={total_parts}&size={len(chunk)}&start={part_num*chunk_size}&end={part_num*chunk_size+len(chunk)}"
            
            # 重试机制
            for retry in range(5):
                try:
                    r = requests.put(part_url, data=chunk, headers={"X-Upos-Auth": auth, **HEADERS}, timeout=60)
                    if r.status_code == 200:
                        break
                except Exception as e:
                    if retry == 4: raise e
                    time.sleep(2)
            
            parts_info.append({"partNumber": part_num + 1})
            pct = (part_num + 1) / total_parts * 100
            print(f"[UPOS] 进度: {pct:.1f}% ({part_num+1}/{total_parts})")
            
    # 4. 完成合并
    finish_url = f"{base_upload_url}?output=json&name={file_name}&profile=ugcupos%2Fbup&uploadId={upload_id}"
    finish_data = {"parts": parts_info}
    finish_res = requests.post(finish_url, json=finish_data, headers={"X-Upos-Auth": auth, **HEADERS}).json()
    print(f"[UPOS] 上传完成！返回: {finish_res}")
    
    # 返回 filename (即分配的 upos_clean_path 中的文件名)
    return os.path.basename(upos_clean_path).split(".")[0]

def submit_manuscript(cookies, title, desc, tags, tid, cover_url, upos_id, source=""):
    """提交稿件（安全阈值 <= 75 字符）"""
    if len(title) > 75:
        print(f"[Manuscript] 警告: 标题过长 ({len(title)} 字)，自动截断至安全范围")
        title = title[:75]
        
    url = f"https://member.bilibili.com/x/vu/web/add?csrf={cookies.get('bili_jct', '')}"
    payload = {
        "copyright": 2 if source else 1,
        "source": source,
        "desc": desc,
        "desc_format_id": 0,
        "dynamic": f"【新片速递】{title}",
        "cover": cover_url,
        "title": title,
        "tag": ",".join(tags),
        "tid": tid,
        "no_reprint": 1 if not source else 0,
        "open_elec": 0,
        "videos": [
            {
                "filename": upos_id,
                "title": title[:50],
                "desc": ""
            }
        ],
        "csrf": cookies.get("bili_jct", "")
    }
    
    res = requests.post(url, json=payload, headers=HEADERS, cookies=cookies).json()
    if res.get("code") == 0:
        bvid = res["data"]["bvid"]
        aid = res["data"]["aid"]
        print(f"🎉 稿件成功提交！BVID: {bvid} | AID: {aid}")
        return bvid, aid
    else:
        raise RuntimeError(f"提交稿件失败: {res}")

def poll_and_pin(cookies, aid, bvid, pin_text, max_attempts=180, poll_interval=20):
    """稳定轮询审核并发表置顶长评"""
    print(f"[Watcher] 启动审核状态稳定监听 (BVID: {bvid})...")
    list_url = "https://member.bilibili.com/x/web/archives?status=is_pubing,pubed,not_pubed&pn=1&ps=10"
    
    is_open = False
    for attempt in range(1, max_attempts + 1):
        try:
            res = requests.get(list_url, headers=HEADERS, cookies=cookies, timeout=10).json()
            audits = res.get("data", {}).get("arc_audits", [])
            for item in audits:
                arc = item.get("Archive", {})
                if arc.get("bvid") == bvid:
                    state = arc.get("state")
                    if state == 0:
                        print(f"🎉 [{bvid}] 审核通过开放浏览！准备发表置顶评论...")
                        is_open = True
                        break
                    else:
                        print(f"[Watcher #{attempt}] {bvid} 审核中 (state={state})...")
                        break
        except Exception as e:
            print(f"[Watcher] 查询异常: {e}")
            
        if is_open:
            break
        time.sleep(poll_interval)
        
    if not is_open:
        print("[Watcher] 超时未完成审核，转由后台异步跟踪。")
        return
        
    # 发表置顶评论
    reply_url = "https://api.bilibili.com/x/v2/reply/add"
    post_data = {
        "type": 1,
        "oid": aid,
        "message": pin_text,
        "csrf": cookies.get("bili_jct", "")
    }
    rep = requests.post(reply_url, data=post_data, headers=HEADERS, cookies=cookies).json()
    rpid = rep.get("data", {}).get("rpid")
    
    if not rpid:
        # 防重回溯
        time.sleep(3)
        chk = requests.get(f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=2", headers=HEADERS, cookies=cookies).json()
        for r in chk.get("data", {}).get("replies", []):
            if str(r.get("mid")) == str(cookies.get("DedeUserID")):
                rpid = r.get("rpid")
                break
                
    if rpid:
        print(f"[Pin] 正在置顶评论 (rpid: {rpid})...")
        pin_url = "https://api.bilibili.com/x/v2/reply/top"
        for i in range(5):
            pin_res = requests.post(pin_url, data={"type": 1, "oid": aid, "rpid": rpid, "action": 1, "csrf": cookies.get("bili_jct", "")}, headers=HEADERS, cookies=cookies).json()
            if pin_res.get("code") == 0:
                print(f"🌟 置顶大获成功！rpid: {rpid}")
                break
            time.sleep(15)
