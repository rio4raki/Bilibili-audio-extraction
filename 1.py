import requests
import re
import os
import time
import hashlib
import subprocess
import shutil
from functools import reduce
from datetime import datetime

# --- WBI 签名实现 (无需更改) ---
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

def get_mixin_key(orig: str):
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, '')[:32]

def get_wbi_keys():
    try:
        resp = requests.get('https://api.bilibili.com/x/web-interface/nav', headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
        })
        resp.raise_for_status()
        json_content = resp.json()
        img_url: str = json_content['data']['wbi_img']['img_url']
        sub_url: str = json_content['data']['wbi_img']['sub_url']
        img_key = img_url.rsplit('/', 1)[1].split('.')[0]
        sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
        return get_mixin_key(img_key + sub_key)
    except Exception as e:
        print(f"获取 WBI 密钥时出错: {e}")
        return None

def sign_wbi_request(params: dict, wbi_key: str):
    params['wts'] = int(time.time())
    sorted_params = sorted(params.items())
    query = '&'.join([f'{k}={v}' for k, v in sorted_params])
    w_rid = hashlib.md5((query + wbi_key).encode()).hexdigest()
    params['w_rid'] = w_rid
    return params

# --- 功能函数 ---
def sanitize_filename(filename: str):
    return re.sub(r'[\\/:*?"<>|]', '_', filename).strip()

def get_video_details(url: str):
    """通过API获取视频的详细信息（BVID, CID, 标题, 发布日期）。"""
    print("正在通过API获取视频详情...")
    bvid_match = re.search(r'(BV[a-zA-Z0-9]{10})', url)
    if not bvid_match:
        print(f"无法从URL中提取BVID: {url}")
        return None, None, None, None
    bvid = bvid_match.group(1)

    api_url = "https://api.bilibili.com/x/web-interface/view"
    params = {'bvid': bvid}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Referer': 'https://www.bilibili.com/'
    }
    try:
        response = requests.get(api_url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data['code'] == 0:
            video_data = data['data']
            title = video_data['title']
            cid = video_data['cid']
            pubdate = video_data['pubdate'] # 【新功能】获取发布日期时间戳
            print(f"已获取视频标题: {title}")
            return bvid, cid, title, pubdate
        else:
            print(f"API返回错误: {data['message']}")
            return None, None, None, None
    except Exception as e:
        print(f"请求视频详情API时出错: {e}")
        return None, None, None, None

def get_play_streams(bvid: str, cid: str, wbi_key: str):
    base_url = "https://api.bilibili.com/x/player/wbi/playurl"
    params = {'bvid': bvid, 'cid': cid, 'fnval': '4048', 'fourk': '1'}
    signed_params = sign_wbi_request(params, wbi_key)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36', 'Referer': f'https://www.bilibili.com/video/{bvid}'}
    print("正在从 API 请求视频流数据...")
    try:
        response = requests.get(base_url, params=signed_params, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"API 请求失败: {e}")
        return None

def select_best_audio(stream_data):
    if not stream_data or stream_data.get('code') != 0:
        if stream_data: print(f"API 返回信息: {stream_data.get('message')}")
        return None
    try:
        audio_streams = stream_data['data']['dash']['audio']
        if not audio_streams: return None
        audio_streams.sort(key=lambda x: x['id'], reverse=True)
        return audio_streams[0]
    except Exception as e:
        print(f"无法从 API 响应中解析音频流: {e}")
        return None

def download_file(url: str, filepath: str, bvid: str):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36', 'Referer': f'https://www.bilibili.com/video/{bvid}'}
    print("开始下载音频文件...")
    try:
        with requests.get(url, headers=headers, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            with open(filepath, 'wb') as f:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    done = int(50 * downloaded / total_size) if total_size > 0 else 0
                    print(f"\r下载中: [{'=' * done}{' ' * (50-done)}] {downloaded / (1024 * 1024):.2f} MB", end='')
        print("\n下载完成！")
        return True
    except Exception as e:
        print(f"\n下载过程中发生错误: {e}")
        return False

def convert_to_mp3_with_progress(temp_file: str, final_file: str):
    print(f"开始格式转换 ({os.path.basename(temp_file)} -> {os.path.basename(final_file)})...")
    if os.path.exists(final_file):
        print(f"文件 '{os.path.basename(final_file)}' 已存在，跳过转换。")
        return

    try:
        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', temp_file]
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
        total_duration = float(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        total_duration = 0

    ffmpeg_cmd = ['ffmpeg', '-i', temp_file, '-vn', '-b:a', '192k', '-y', final_file]
    
    try:
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8')
        
        for line in process.stdout:
            if total_duration > 0:
                time_match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
                if time_match:
                    h, m, s, ms = map(int, time_match.groups())
                    current_seconds = h * 3600 + m * 60 + s + ms / 100
                    percent = min((current_seconds / total_duration) * 100, 100)
                    done = int(50 * percent / 100)
                    print(f"\r转换中: [{'=' * done}{' ' * (50-done)}] {percent:.2f}%", end='')
        
        process.wait()
        if process.returncode == 0:
            if total_duration > 0:
                 print(f"\r转换中: [{'=' * 50}] 100.00%", end='\n')
            print("格式转换成功！")
        else:
            print(f"\nffmpeg转换失败，返回代码: {process.returncode}")
    except FileNotFoundError:
        print("\n错误: ffmpeg 命令未找到。请确保已正确安装并配置环境变量。")
    except Exception as e:
        print(f"\n转换过程中发生未知错误: {e}")

def process_url(url: str, wbi_key: str, download_dir: str):
    """处理单个URL的完整流程"""
    print("-" * 60)
    print(f"正在处理 URL: {url}")
    
    bvid, cid, title, pubdate = get_video_details(url)
    if not (bvid and cid and title):
        print(f"跳过此URL: {url}")
        return

    stream_data = get_play_streams(bvid, cid, wbi_key)
    best_audio_stream = select_best_audio(stream_data)

    if best_audio_stream:
        audio_url = best_audio_stream['baseUrl']
        
        temp_filename = f"temp_{bvid}.m4a"
        temp_filepath = os.path.join(download_dir, temp_filename)
        
        # 【新功能】格式化日期并创建最终文件名
        date_string = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d %H.%M.%S")
        # 将文件名中可能存在的":"替换为"."以兼容Windows系统
        final_filename = f"{sanitize_filename(title)}_{date_string}.mp3"
        final_filepath = os.path.join(download_dir, final_filename)
        
        if download_file(audio_url, temp_filepath, bvid):
            convert_to_mp3_with_progress(temp_filepath, final_filepath)
        
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                print(f"已清理临时文件: {temp_filename}")
            except OSError as e:
                print(f"删除临时文件失败: {e}")
    else:
        print("未能获取到合适的音频流进行下载。")

def main():
    """主函数，用于批量运行下载器。"""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("错误: 未在您的系统中找到 'ffmpeg' 或 'ffprobe'。")
        print("请确保您已正确安装FFmpeg，并将其添加至系统环境变量中。")
        input("按 Enter 键退出。")
        return

    input_file = "get.txt"
    if not os.path.exists(input_file):
        print(f"错误：找不到输入文件 '{input_file}'。")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        urls = f.readlines()
    
    if not any(url.strip() for url in urls):
        print(f"错误：输入文件 '{input_file}' 为空。")
        return

    print("获取最新的 WBI 密钥...")
    wbi_key = get_wbi_keys()
    if not wbi_key:
        print("无法获取WBI密钥，程序终止。")
        return
    print("WBI 密钥获取成功。")
    
    download_dir = "download"
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    for url in urls:
        url = url.strip()
        if url:
            process_url(url, wbi_key, download_dir)
    
    print("-" * 60)
    print("所有任务已处理完毕。")

if __name__ == "__main__":
    main()