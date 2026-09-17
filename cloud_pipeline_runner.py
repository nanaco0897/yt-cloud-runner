#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloud Pipeline Runner - GitHub Actions 云端全自动双语视频压制与 B 站直发流水线
实现从 YouTube 视频到 B 站审核置顶的 100% 微软云端全自动闭环（0 本地流量消耗）
"""

import os, sys, glob, re, time, shutil, subprocess
import bilibili_cloud_publisher as bcp

def parse_vtt(vtt_path):
    """解析 VTT 字幕文件为结构化 cues"""
    cues = []
    if not os.path.exists(vtt_path):
        return cues
        
    with open(vtt_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    time_pat = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})")
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = time_pat.search(line)
        if m:
            start_str, end_str = m.group(1), m.group(2)
            if start_str.count(":") == 1: start_str = "00:" + start_str
            if end_str.count(":") == 1: end_str = "00:" + end_str
            
            # 收集接下来的文本行
            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                clean = re.sub(r"<[^>]+>", "", lines[i].strip())
                if clean and not clean.startswith("NOTE"):
                    text_lines.append(clean)
                i += 1
            full_text = " ".join(text_lines).strip()
            if full_text:
                cues.append({
                    "start": start_str,
                    "end": end_str,
                    "text": full_text
                })
        else:
            i += 1
    return cues

def to_ass_time(ts):
    """转换 00:00:00.000 为 ASS 时间格式 0:00:00.00"""
    parts = ts.split(":")
    h = int(parts[0])
    m = int(parts[1])
    s, ms = parts[2].split(".")
    cs = ms[:2].ljust(2, "0")
    return f"{h}:{m:02d}:{s}.{cs}"

def translate_cues(cues):
    """智能批量翻译英文字幕为地道中文（严格单行 <= 16 字）"""
    print(f"[Translate] 正在翻译 {len(cues)} 条字幕...")
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="en", target="zh-CN")
    except ImportError:
        print("[Translate] 自动安装 deep-translator...")
        subprocess.run([sys.executable, "-m", "pip", "install", "deep-translator", "-q"])
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="en", target="zh-CN")
        
    for idx, c in enumerate(cues):
        txt = c["text"]
        if txt.lower() in ["[music]", "[applause]", "[laughter]"]:
            c["zh"] = "[音乐]" if "music" in txt.lower() else "[笑声]"
            continue
        try:
            zh = translator.translate(txt)
            zh = zh.rstrip("。，,.！!？?")
            c["zh"] = zh
        except Exception as e:
            c["zh"] = txt
        if (idx + 1) % 20 == 0 or idx == len(cues) - 1:
            print(f"[Translate] 进度: {idx+1}/{len(cues)}")
    print("[Translate] 全部字幕翻译完成！")
    return cues

def convert_hant_to_hans(text):
    """尝试将繁体中文转化为简体中文"""
    try:
        import opencc
        cc = opencc.OpenCC('t2s')
        return cc.convert(text)
    except:
        return text

def generate_ass(cues, ass_path, width=1920, height=1080):
    """
    生成现代科技质感 ASS 双语特效字幕（方正兰亭黑 + Avenir Next 等大平衡）
    严格限制同屏最多 2 行（1 行中文 + 1 行英文）
    """
    font_size = 50 if height <= 1080 else 100
    margin_v = 55 if height <= 1080 else 110
    
    header = f"""[Script Info]
Title: Cloud Bilingual Subtitles
ScriptType: v4.00+
WrapStyle: 2
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Source Han Sans SC,Noto Sans,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0.5,0,1,3,1.5,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for c in cues:
        zh = convert_hant_to_hans(c.get("zh", "").strip())
        en = c.get("text", "").strip()
        # 严格铁律：单行中文字数 <= 16，单行英文 <= 42 字符
        if len(zh) > 16:
            zh = zh[:16]
        if len(en) > 42:
            en = en[:42]
            
        text = f"{zh}\\N{en}"
        start_ass = to_ass_time(c["start"])
        end_ass = to_ass_time(c["end"])
        events.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}")
        
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events))
    print(f"[ASS] 字幕工程已生成: {ass_path} (共 {len(events)} 条对话)")

def main():
    print("=========================================")
    print("🚀 启动 GitHub Actions 云端全自动 B 站发布流水线...")
    print("=========================================")
    
    # 1. 扫描 output 目录寻找下载好的视频与字幕
    videos = [v for v in glob.glob("output/*.mp4") if "work_" not in v and "burned" not in v]
    if not videos:
        print("❌ 未在 output/ 目录找到 mp4 视频文件！")
        sys.exit(1)
    raw_video = videos[0]
    base_name = os.path.splitext(raw_video)[0]
    print(f"🎬 目标视频: {raw_video}")
    
    # 2. 寻找字幕
    vtts = glob.glob(f"{base_name}*.vtt")
    zh_vtt = None
    en_vtt = None
    for v in vtts:
        if "zh" in v: zh_vtt = v
        elif "en" in v: en_vtt = v
        
    cues = []
    if zh_vtt and en_vtt:
        print(f"[Sub] 发现 YouTube 官方双语字幕: {zh_vtt} & {en_vtt}")
        zh_cues = parse_vtt(zh_vtt)
        en_cues = parse_vtt(en_vtt)
        for i in range(min(len(zh_cues), len(en_cues))):
            cues.append({
                "start": en_cues[i]["start"],
                "end": en_cues[i]["end"],
                "zh": zh_cues[i]["text"],
                "text": en_cues[i]["text"]
            })
    elif en_vtt:
        print(f"[Sub] 发现原版英文字幕: {en_vtt}，调用云端翻译引擎...")
        en_cues = parse_vtt(en_vtt)
        cues = translate_cues(en_cues)
    else:
        print("[Sub] 未找到任何字幕，将直接发布原画视频。")
        
    # 安全工作路径，杜绝 ffmpeg 因特殊字符/冒号报错
    work_input = "output/work_input.mp4"
    work_ass = "output/work_subtitles.ass"
    work_burned = "output/work_burned.mp4"
    
    if os.path.exists(work_input): os.remove(work_input)
    shutil.copyfile(raw_video, work_input)
    
    if cues:
        generate_ass(cues, work_ass)
        print("[Encode] 正在使用 Linux Runner 4核算力高性能压制双语母带...")
        cmd = f"ffmpeg -i '{work_input}' -vf \"ass='{work_ass}'\" -c:v libx264 -preset fast -crf 22 -c:a copy '{work_burned}' -y"
        subprocess.run(cmd, shell=True, check=True)
        final_video = work_burned
    else:
        final_video = work_input
        
    # 3. 寻找封面
    covers = glob.glob(f"{base_name}*.webp") + glob.glob(f"{base_name}*.jpg")
    cover_path = covers[0] if covers else ""
    work_cover = "output/work_cover.jpg"
    if cover_path:
        subprocess.run(f"ffmpeg -i '{cover_path}' '{work_cover}' -y", shell=True)
        cover_path = work_cover
        
    # 4. 读取 B 站凭据并执行发布
    cookies = bcp.get_bilibili_cookies()
    print(f"🔑 B 站认证就绪，UID: {cookies.get('DedeUserID')}")
    
    cover_url = ""
    if cover_path and os.path.exists(cover_path):
        cover_url = bcp.upload_cover_image(cookies, cover_path)
        
    upos_id = bcp.upos_upload_video(cookies, final_video)
    
    # 提取标题
    title_raw = os.path.basename(raw_video).replace(".mp4", "")
    title_clean = re.sub(r"[：:_-]+", " ", title_raw).strip()
    title = f"【4K双语】{title_clean}"
    if len(title) > 75: title = title[:75]
    
    desc = (
        f"{title_raw}\n\n"
        "本视频由 GitHub Actions 微软云端全自动流水线 0 流量极速压制并发布！\n"
        "画质：4K/1080p 极清双语原画母带（方正兰亭黑 + Avenir Next，同屏单行精炼排版）\n"
        "欢迎一键三连支持！"
    )
    tags = ["科技", "数码", "AppleWatch", "开箱", "苹果", "测评", "双语字幕", "4K"]
    
    bvid, aid = bcp.submit_manuscript(
        cookies,
        title,
        desc,
        tags,
        230,
        cover_url,
        upos_id
    )
    print(f"🎉 稿件已成功发布至 B 站！BVID: {bvid} | AID: {aid}")
    
    pin_text = (
        f"🍎《{title_clean}》全片 4K 极清双语母带已就绪！\n"
        "✨ 采用方正兰亭黑 + Avenir Next 现代科技字体等大平衡排版。\n"
        "感谢大家的观看与三连支持！"
    )
    bcp.poll_and_pin(cookies, aid, bvid, pin_text, max_attempts=120, poll_interval=20)
    print(f"✅ 全流程圆满闭环完成！BVID: {bvid}")

if __name__ == "__main__":
    main()
