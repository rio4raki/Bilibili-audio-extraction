import requests
import re
import os
import time
import hashlib
import subprocess
import shutil
from functools import reduce
from datetime import datetime
import configparser

# --- Configuration and Logging Setup (New) ---
CONFIG_FILE = 'config.ini'
LOG_FILE = 'download_log.txt'

def create_default_config():
    """Creates a default config.ini file if it doesn't exist."""
    if not os.path.exists(CONFIG_FILE):
        print(f"配置文件 '{CONFIG_FILE}' 不存在，正在创建默认配置...")
        config = configparser.ConfigParser()
        config['Settings'] = {
            '# 下载选项: 1 = 仅视频, 2 = 仅音频': '',
            'download_choice': '2',
            '# 如果下载音频，是否自动转换为MP3格式 (true/false)': '',
            'convert_to_mp3': 'true',
            '# 是否在文件名后添加日期 (true/false)': '',
            'add_date_watermark': 'true',
            '# 是否开启重复下载检测 (true/false)': '',
            'duplicate_detection': 'true'
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as configfile:
            config.write(configfile)
        print(f"默认配置文件 '{CONFIG_FILE}' 已创建。您可以根据需要修改它。")

def load_config():
    """Loads settings from config.ini."""
    if not os.path.exists(CONFIG_FILE):
        create_default_config()
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding='utf-8')
    settings = {
        'choice': config.get('Settings', 'download_choice', fallback='2'),
        'mp3': config.getboolean('Settings', 'convert_to_mp3', fallback=True),
        'date': config.getboolean('Settings', 'add_date_watermark', fallback=True),
        'detect_duplicates': config.getboolean('Settings', 'duplicate_detection', fallback=True)
    }
    return settings

def read_log():
    """Reads the log file and returns a set of BVIDs of downloaded files."""
    if not os.path.exists(LOG_FILE):
        return set()
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f)

def write_to_log(bvid):
    """Writes a successfully downloaded BVID to the log file."""
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{bvid}\n")

# --- WBI Signature Implementation (Unchanged) ---
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

# --- Feature Functions ---
def sanitize_filename(filename: str):
    return re.sub(r'[\\/:*?"<>|]', '_', filename).strip()

def get_video_details(url: str):
    """Gets detailed video information (BVID, CID, Title, Pubdate) via API."""
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
            pubdate = video_data['pubdate']
            print(f"已获取视频标题: {title}")
            return bvid, cid, title, pubdate
        else:
            print(f"API返回错误: {data['message']}")
            return None, None, None, None
    except Exception as e:
        print(f"请求视频详情API时出错: {e}")
        return None, None, None, None

def get_play_streams(bvid: str, cid: str, wbi_key: str):
    """Gets video stream data using the WBI-signed API."""
    base_url = "https://api.bilibili.com/x/player/wbi/playurl"
    # fnval=4048 requests all available DASH streams
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

def select_best_stream(stream_data, stream_type='video'):
    """Selects the best available video or audio stream."""
    if not stream_data or stream_data.get('code') != 0:
        if stream_data: print(f"API 返回信息: {stream_data.get('message')}")
        return None
    try:
        # The 'dash' object contains separate lists for 'video' and 'audio' streams
        streams = stream_data['data']['dash'][stream_type]
        if not streams: return None
        # Sorting by 'id' which corresponds to quality (e.g., 120 for 4K, 30280 for 192K audio)
        streams.sort(key=lambda x: x['id'], reverse=True)
        return streams[0]
    except Exception as e:
        print(f"无法从 API 响应中解析 {stream_type} 流: {e}")
        return None

def download_file(url: str, filepath: str, bvid: str, file_type: str):
    """Downloads a file with progress."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36', 'Referer': f'https://www.bilibili.com/video/{bvid}'}
    print(f"开始下载 {file_type} 文件...")
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
    """使用Popen实时显示进度，并将音频文件转换为MP3。"""
    print(f"开始格式转换 ({os.path.basename(temp_file)} -> {os.path.basename(final_file)})...")
    if os.path.exists(final_file):
        print(f"文件 '{os.path.basename(final_file)}' 已存在，跳过转换。")
        return True

    # 尝试使用 ffprobe 获取总时长，用于计算百分比
    total_duration = 0
    try:
        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', temp_file]
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
        total_duration = float(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        print("警告: 无法获取视频总时长，将不显示转换百分比。")

    # 使用 Popen 来执行 ffmpeg 命令以实时捕获输出
    ffmpeg_cmd = ['ffmpeg', '-i', temp_file, '-vn', '-b:a', '192k', '-y', final_file]
    
    try:
        # 使用 Popen 启动子进程，并将stderr重定向到stdout
        # 指定 encoding='utf-8' 和 errors='ignore' 来解决解码错误
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                                   universal_newlines=True, encoding='utf-8', errors='ignore')
        
        # 实时读取输出行
        for line in process.stdout:
            if total_duration > 0:
                # 从FFmpeg的输出中匹配时间
                time_match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
                if time_match:
                    h, m, s, ms = map(int, time_match.groups())
                    current_seconds = h * 3600 + m * 60 + s + ms / 100
                    percent = min((current_seconds / total_duration) * 100, 100)
                    done = int(50 * percent / 100)
                    print(f"\r转换中: [{'=' * done}{' ' * (50-done)}] {percent:.2f}%", end='')
        
        process.wait() # 等待进程结束
        if process.returncode == 0:
            if total_duration > 0:
                 print(f"\r转换中: [{'=' * 50}] 100.00%", end='\n')
            print("格式转换成功！")
            return True
        else:
            print(f"\nffmpeg转换失败，返回代码: {process.returncode}")
            return False
    except FileNotFoundError:
        print("\n错误: ffmpeg 命令未找到。请确保已正确安装并配置环境变量。")
        return False
    except Exception as e:
        print(f"\n转换过程中发生未知错误: {e}")
        return False
    """Converts the downloaded audio file to MP3 using ffmpeg."""
    print(f"开始格式转换 ({os.path.basename(temp_file)} -> {os.path.basename(final_file)})...")
    if os.path.exists(final_file):
        print(f"文件 '{os.path.basename(final_file)}' 已存在，跳过转换。")
        return True

    ffmpeg_cmd = ['ffmpeg', '-i', temp_file, '-vn', '-b:a', '192k', '-y', final_file]
    try:
        process = subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True)
        print("格式转换成功！")
        return True
    except FileNotFoundError:
        print("\n错误: ffmpeg 命令未找到。请确保已正确安装并配置环境变量。")
        return False
    except subprocess.CalledProcessError as e:
        print(f"\nffmpeg转换失败，返回代码: {e.returncode}\n{e.stderr}")
        return False
    except Exception as e:
        print(f"\n转换过程中发生未知错误: {e}")
        return False

def merge_video_audio(video_path: str, audio_path: str, final_path: str):
    """Merges video and audio streams using ffmpeg."""
    print(f"开始合并音视频 -> {os.path.basename(final_path)}...")
    if os.path.exists(final_path):
        print(f"文件 '{os.path.basename(final_path)}' 已存在，跳过合并。")
        return True
        
    ffmpeg_cmd = ['ffmpeg', '-i', video_path, '-i', audio_path, '-c', 'copy', '-y', final_path]
    try:
        process = subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True, encoding='utf-8')
        print("音视频合并成功！")
        return True
    except FileNotFoundError:
        print("\n错误: ffmpeg 命令未找到。请确保已正确安装并配置环境变量。")
        return False
    except subprocess.CalledProcessError as e:
        print(f"\nffmpeg合并失败，返回代码: {e.returncode}\n{e.stderr}")
        return False
    except Exception as e:
        print(f"\n合并过程中发生未知错误: {e}")
        return False

def cleanup_temp_files(*files):
    """Removes temporary files."""
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"已清理临时文件: {os.path.basename(f)}")
            except OSError as e:
                print(f"删除临时文件失败: {e}")

def process_url(url: str, wbi_key: str, config: dict, downloaded_bvs: set):
    """Processes a single URL based on the loaded configuration."""
    print("-" * 60)
    print(f"正在处理 URL: {url}")
    
    bvid, cid, title, pubdate = get_video_details(url)
    if not (bvid and cid and title):
        print(f"跳过此URL (无法获取详情): {url}")
        return

    # --- Duplicate Detection (New) ---
    if config['detect_duplicates'] and bvid in downloaded_bvs:
        print(f"\n警告: 视频 '{title}' (BVID: {bvid}) 已存在于日志中，可能为重复下载。")
        while True:
            choice = input("请选择操作: [1] 跳过 [2] 继续下载 (默认为1): ").strip()
            if choice == '2':
                print("选择继续下载。")
                break
            elif choice in ('1', ''):
                print("选择跳过。")
                return
            else:
                print("无效输入，请重新输入。")

    stream_data = get_play_streams(bvid, cid, wbi_key)
    
    # --- Filename Generation (New Logic) ---
    base_filename = sanitize_filename(title)
    if config['date']:
        date_string = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d")
        base_filename = f"{base_filename}_{date_string}"

    download_dir = "download"
    download_successful = False

    # --- Download Logic based on Choice (New) ---
    if config['choice'] == '1': # Download Video
        print("模式: 下载视频")
        video_stream = select_best_stream(stream_data, 'video')
        audio_stream = select_best_stream(stream_data, 'audio')

        if video_stream and audio_stream:
            temp_video_path = os.path.join(download_dir, f"temp_{bvid}_vid.m4s")
            temp_audio_path = os.path.join(download_dir, f"temp_{bvid}_aud.m4a")
            final_filepath = os.path.join(download_dir, f"{base_filename}.mp4")
            
            vid_ok = download_file(video_stream['baseUrl'], temp_video_path, bvid, "视频")
            aud_ok = download_file(audio_stream['baseUrl'], temp_audio_path, bvid, "音频")

            if vid_ok and aud_ok:
                if merge_video_audio(temp_video_path, temp_audio_path, final_filepath):
                    download_successful = True
            
            cleanup_temp_files(temp_video_path, temp_audio_path)
        else:
            print("未能获取到合适的视频或音频流进行下载。")

    elif config['choice'] == '2': # Download Audio
        print("模式: 下载音频")
        audio_stream = select_best_stream(stream_data, 'audio')
        if audio_stream:
            temp_audio_path = os.path.join(download_dir, f"temp_{bvid}.m4a")
            
            if download_file(audio_stream['baseUrl'], temp_audio_path, bvid, "音频"):
                if config['mp3']:
                    final_filepath = os.path.join(download_dir, f"{base_filename}.mp3")
                    if convert_to_mp3_with_progress(temp_audio_path, final_filepath):
                        download_successful = True
                else:
                    final_filepath = os.path.join(download_dir, f"{base_filename}.m4a")
                    shutil.move(temp_audio_path, final_filepath)
                    download_successful = True

            cleanup_temp_files(temp_audio_path)
        else:
            print("未能获取到合适的音频流进行下载。")
    
    # --- Log Success (New) ---
    if download_successful:
        write_to_log(bvid)
        print(f"成功处理 BVID: {bvid}")
    else:
        print(f"处理 BVID: {bvid} 失败。")


def main():
    """Main function to run the downloader."""
    if not shutil.which("ffmpeg"):
        print("错误: 未在您的系统中找到 'ffmpeg'。")
        print("请确保您已正确安装FFmpeg，并将其添加至系统环境变量中。")
        input("按 Enter 键退出。")
        return

    config = load_config()
    downloaded_bvs = read_log()

    input_file = "get.txt"
    if not os.path.exists(input_file):
        with open(input_file, 'w', encoding='utf-8') as f:
             f.write("# 请在此文件中输入B站视频链接，每行一个\n")
        print(f"错误：找不到输入文件 '{input_file}'。已为您创建一个空文件，请填入链接后重新运行。")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        urls = [line for line in (l.strip() for l in f) if line and not line.startswith('#')]
    
    if not urls:
        print(f"错误：输入文件 '{input_file}' 为空或只包含注释。")
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
        process_url(url, wbi_key, config, downloaded_bvs)
    
    print("-" * 60)
    print("所有任务已处理完毕。")

if __name__ == "__main__":
    main()