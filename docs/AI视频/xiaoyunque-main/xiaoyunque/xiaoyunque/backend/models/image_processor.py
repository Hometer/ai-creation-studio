import os
import sys

models_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(models_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import requests
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from PIL import Image
import logging
from config import Config

logger = logging.getLogger(__name__)


class ImageProcessor:
    """
    图片处理和上传集合类
    支持：图片处理、分割、拼接，以及上传到阿里云OSS
    """
    
    # 阿里云DashScope上传配置
    UPLOAD_API_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
    
    def __init__(self,
                 image_path='',
                 api_key: str = "",
                 model_name: str = "wan2.6-i2v-flash"):
        """
        初始化图片处理器
        
        Args:
            image_path: 图片文件路径（可选，用于处理已有图片）
            api_key: DashScope API Key（用于上传，可从 config.yaml 读取）
            model_name: 模型名称，默认使用 wan2.6-i2v-flash
        """
        # 图片处理部分
        if image_path != '':
            self.image_path = image_path
            self.image = Image.open(image_path)
            self.image_np = np.array(self.image)
            self.width, self.height = self.image_np.shape[1], self.image_np.shape[0]
        else:
            self.image_path = None
            self.image = None
            self.image_np = None
            self.width = None
            self.height = None
        
        # 上传功能部分
        self.api_key = api_key or Config.DASHSCOPE_API_KEY
        self.model_name = model_name

    @staticmethod
    def check_column_white(column_pixels):
        """检查列是否几乎全白"""
        is_almost_white = np.logical_or(column_pixels == 254, column_pixels == 255)
        white_pixels_ratio = np.mean(np.all(is_almost_white, axis=-1))
        return white_pixels_ratio >= 0.98  # 至少98%的像素为白色

    def find_white_section(self, start, end):
        """查找指定范围内的白色区间"""
        white_sections = []
        in_white_section = False
        start_index = 0

        for col in range(start, end):
            column_pixels = self.image_np[:, col, :]
            if self.check_column_white(column_pixels):
                if not in_white_section:
                    start_index = col
                    in_white_section = True
            else:
                if in_white_section:
                    white_sections.append((start_index, col))
                    in_white_section = False

        if in_white_section:
            white_sections.append((start_index, end))

        return white_sections

    def split_image(self):
        """将图片从中间分割为左右两部分"""
        start_col = self.width * 2 // 5
        end_col = self.width * 3 // 5
        white_sections = self.find_white_section(start_col, end_col)

        if white_sections:
            middle_section = white_sections[len(white_sections) // 2]
            mid_col = (middle_section[0] + middle_section[1]) // 2
        else:
            raise ValueError("No suitable white column found within the specified range")

        left_box = (0, 0, mid_col, self.height)
        right_box = (mid_col, 0, self.width, self.height)
        left_image = self.image.crop(left_box)
        right_image = self.image.crop(right_box)

        save_dir, filename = os.path.split(self.image_path)
        base, extension = os.path.splitext(filename)

        left_image_path = os.path.join(save_dir, base + '_front' + extension)
        right_image_path = os.path.join(save_dir, base + '_back' + extension)
        left_image.save(left_image_path)
        right_image.save(right_image_path)

        return left_image_path, right_image_path
    
    def stitch_images(self, image_paths, output_path):
        """拼接多张图片"""
        if not image_paths:
            raise ValueError("No image paths provided")
        sample_image = Image.open(image_paths[0])
        single_width, single_height = sample_image.size
        num_images = len(image_paths)
        total_desired_width = single_width
        total_current_width = single_width * num_images
        total_width_to_cut = max(0, total_current_width - total_desired_width)
        width_to_cut_per_image = total_width_to_cut // num_images
        stitched_image = Image.new('RGB', (total_desired_width, single_height), "white")
        current_x = 0
        
        for path in image_paths:
            image = Image.open(path)
            if width_to_cut_per_image > 0:
                left_margin = width_to_cut_per_image // 2
                right_margin = image.width - width_to_cut_per_image + left_margin
                image = image.crop((left_margin, 0, right_margin, image.height))
            stitched_image.paste(image, (current_x, 0))
            current_x += image.width
        
        output_dir = os.path.dirname(output_path)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        stitched_image.save(output_path)
        return output_path
    
    def download_image(self, image_url, save_path, max_retries=3, proxies=None):
        """Download with verified TLS and atomic replacement; never downgrade TLS."""
        import tempfile
        import time
        from models.request_safety import ModelRequestError

        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 1:
            raise ModelRequestError("图片下载重试配置无效。")
        destination = Path(save_path)
        for attempt in range(1, max_retries + 1):
            response = None
            temporary = None
            try:
                response = requests.get(
                    image_url, timeout=(10, 30), stream=True, verify=True,
                    headers={"User-Agent": "Mozilla/5.0"}, proxies=proxies,
                )
                if response.status_code == 200:
                    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".image-", delete=False) as file:
                        temporary = Path(file.name)
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                file.write(chunk)
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(temporary, destination)
                    logger.info("Image download completed")
                    return True
                logger.warning("Image download rejected: status=%d attempt=%d/%d",
                               response.status_code, attempt, max_retries)
                if response.status_code < 500 and response.status_code not in {408, 429}:
                    return False
            except requests.exceptions.SSLError:
                logger.warning("Image download certificate verification failed")
                raise ModelRequestError("图片下载证书校验失败，已停止下载。") from None
            except (requests.exceptions.RequestException, OSError) as exc:
                logger.warning("Image download failed: category=%s attempt=%d/%d",
                               type(exc).__name__, attempt, max_retries)
                if attempt == max_retries:
                    raise ModelRequestError("图片下载失败，已停止重试。") from None
            finally:
                if response is not None:
                    response.close()
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            if attempt < max_retries:
                time.sleep(attempt * 2)
        return False

    def resize_image(self, image_path):
        """调整图片大小（添加顶部空白）"""
        original_image = Image.open(image_path)
        width, height = original_image.size
        top_blank_height = height // 2
        final_height = height + top_blank_height
        final_width = int(final_height * 5 / 3)
        new_image = Image.new("RGB", (final_width, final_height), color="white")
        left = (final_width - width) // 2
        top = top_blank_height
        new_image.paste(original_image, (left, top))
        new_image.save(image_path)
        return image_path

    def has_black_borders(self, image_path, threshold=10, black_limit=20):
        """检查图片是否有黑色边框"""
        img = Image.open(image_path)
        pixels = img.load()
        width, height = img.size
        
        def is_black_pixel(pixel):
            return all(x <= black_limit for x in pixel)
        
        # 检查顶部和底部边框
        for y in range(threshold):
            if all(is_black_pixel(pixels[x, y]) for x in range(width)):
                return True
            if all(is_black_pixel(pixels[x, height - 1 - y]) for x in range(width)):
                return True
        
        # 检查左右边框
        for x in range(threshold):
            if all(is_black_pixel(pixels[x, y]) for y in range(height)):
                return True
            if all(is_black_pixel(pixels[width - 1 - x, y]) for y in range(height)):
                return True
        
        return False

    # ===== 图片上传功能 =====
    
    def get_upload_policy(self):
        """
        获取文件上传凭证
        
        Returns:
            policy_data: 包含上传所需凭证的字典
            
        Raises:
            Exception: 获取上传凭证失败时
        """
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未设置，无法使用图片上传服务")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        params = {
            "action": "getPolicy",
            "model": self.model_name
        }
        
        response = requests.get(
            self.UPLOAD_API_URL,
            headers=headers,
            params=params,
            proxies=Config.requests_proxies("dashscope"),
        )
        if response.status_code != 200:
            raise Exception(f"Failed to get upload policy: {response.text}")
        
        return response.json()['data']
    
    def upload_file_to_oss(self, policy_data: dict, file_path: str) -> str:
        """
        将文件上传到临时存储OSS
        
        Args:
            policy_data: 上传凭证数据
            file_path: 本地文件路径
            
        Returns:
            oss_url: OSS URL (格式: oss://...)
            
        Raises:
            Exception: 上传失败时
        """
        file_name = Path(file_path).name
        # Sanitize filename for upload to avoid issues with spaces/characters
        safe_file_name = "".join([c if c.isalnum() or c in ('-','_','.') else '_' for c in file_name])
        
        key = f"{policy_data['upload_dir']}/{safe_file_name}"
        
        with open(file_path, 'rb') as file:
            files = {
                'OSSAccessKeyId': (None, policy_data['oss_access_key_id']),
                'Signature': (None, policy_data['signature']),
                'policy': (None, policy_data['policy']),
                'x-oss-object-acl': (None, policy_data['x_oss_object_acl']),
                'x-oss-forbid-overwrite': (None, policy_data['x_oss_forbid_overwrite']),
                'key': (None, key),
                'success_action_status': (None, '200'),
                'file': (safe_file_name, file)
            }
            
            response = requests.post(
                policy_data['upload_host'],
                files=files,
                proxies=Config.requests_proxies("dashscope"),
            )
            if response.status_code != 200:
                raise Exception(f"Failed to upload file: {response.text}")
        
        # Construct OSS URL correctly: oss://<bucket>/<key>
        # Extract bucket from upload_host (e.g., https://dashscope-instant.oss-cn-beijing.aliyuncs.com)
        upload_host = policy_data['upload_host']
        bucket_name = ""
        if '://' in upload_host:
            domain = upload_host.split('://')[1]
            bucket_name = domain.split('.')[0]
        
        if bucket_name:
            return f"oss://{bucket_name}/{key}"
        else:
            # Fallback if parsing fails (though unlikely for standard OSS hosts)
            # If the original code's assumption that key was self-sufficient was somehow valid, logic is here.
            # But normally, oss://<key> is wrong if key doesn't have bucket.
            return f"oss://{key}"
    
    def upload(self, file_path: str) -> str:
        """
        上传文件到阿里云OSS并获取URL（统一接口方法）
        
        Args:
            file_path: 本地文件路径
            
        Returns:
            oss_url: OSS URL，可在48小时内使用
            
        Raises:
            FileNotFoundError: 文件不存在时
            RuntimeError: API Key未设置时
            Exception: 上传失败时
        """
        # 检查文件是否存在
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未设置，无法使用图片上传服务")
        
        # 1. 获取上传凭证（注意：上传凭证接口有限流）
        policy_data = self.get_upload_policy()
        
        # 2. 上传文件到OSS
        oss_url = self.upload_file_to_oss(policy_data, file_path)
        
        # 3. 计算过期时间
        expire_time = datetime.now() + timedelta(hours=48)
        
        logger.info(
            "File uploaded to OSS: expires_at=%s",
            expire_time.strftime('%Y-%m-%d %H:%M:%S'),
        )
        
        return oss_url

    def collage_images(self, image_paths, output_path):
        """
        拼图功能：将多张图片水平拼接
        Args:
            image_paths: 图片路径列表
            output_path: 输出文件路径
        """
        if not image_paths:
            return None
        
        images = []
        for p in image_paths:
            try:
                img = Image.open(p)
                images.append(img)
            except Exception as e:
                logger.error("Cannot open image (%s)", type(e).__name__)
        
        if not images:
            return None

        # 统一高度，按第一张图片的高度调整其他图片
        base_height = images[0].height
        resized_images = []
        for img in images:
            if img.height != base_height:
                ratio = base_height / img.height
                new_width = int(img.width * ratio)
                resized_images.append(img.resize((new_width, base_height)))
            else:
                resized_images.append(img)
        
        total_width = sum(img.width for img in resized_images)
        new_im = Image.new('RGB', (total_width, base_height))
        
        x_offset = 0
        for img in resized_images:
            new_im.paste(img, (x_offset, 0))
            x_offset += img.width
            
        new_im.save(output_path)
        return output_path
